
"""
Timer Dashboard + YouTube Audio

- Timer cards in a normal grid layout
- YouTube audio panel with queue (add/remove/reorder/next/prev)
- Audio cached in ./cache via yt-dlp
- Timer layout persisted in layout.json

Run:
    uv run python main.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

try:
    import yt_dlp  # type: ignore
except Exception:
    yt_dlp = None

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
except Exception:
    QAudioOutput = None
    QMediaPlayer = None


VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
PARTIAL_CACHE_SUFFIXES = (".part", ".ytdl", ".tmp", ".temp")
EXE_BASE_NAME = "TimerDashboard"


def format_hms(total_seconds: int) -> str:
    total_seconds = max(0, int(total_seconds))
    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def format_duration_short(seconds: int | None) -> str:
    if not seconds:
        return "00:00"
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02}:{minutes:02}:{secs:02}"
    return f"{minutes:02}:{secs:02}"


def extract_video_id(url: str) -> str | None:
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return None

    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]

    video_id = None
    if host == "youtu.be":
        segment = parsed.path.strip("/").split("/", 1)[0]
        video_id = segment or None
    elif host in {"youtube.com", "m.youtube.com", "music.youtube.com"}:
        path_parts = [p for p in parsed.path.split("/") if p]
        if parsed.path == "/watch":
            query = parse_qs(parsed.query)
            video_id = (query.get("v") or [None])[0]
        elif path_parts and path_parts[0] in {"shorts", "embed", "live"} and len(path_parts) > 1:
            video_id = path_parts[1]

    if video_id and VIDEO_ID_PATTERN.match(video_id):
        return video_id
    return None


def is_valid_youtube_url(url: str) -> bool:
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return False

    if parsed.scheme not in {"http", "https"}:
        return False

    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]

    if host not in {"youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}:
        return False

    return extract_video_id(url) is not None


def canonical_watch_url(url: str) -> str:
    video_id = extract_video_id(url)
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    return url.strip()


def find_cached_audio(cache_dir: Path, video_id: str) -> Path | None:
    for path in sorted(cache_dir.glob(f"{video_id}.*")):
        if not path.is_file():
            continue
        if path.name.lower().endswith(PARTIAL_CACHE_SUFFIXES):
            continue
        return path.resolve()
    return None


def cleanup_partial_cache_files(cache_dir: Path, video_id: str) -> None:
    for path in cache_dir.glob(f"{video_id}.*"):
        if not path.is_file():
            continue
        if not path.name.lower().endswith(PARTIAL_CACHE_SUFFIXES):
            continue
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


def cleanup_all_partial_cache_files(cache_dir: Path) -> None:
    for path in cache_dir.iterdir():
        if not path.is_file():
            continue
        if not path.name.lower().endswith(PARTIAL_CACHE_SUFFIXES):
            continue
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


@dataclass
class TimerSnapshot:
    timer_id: str
    title: str
    total_seconds: int
    remaining_seconds: int
    is_running: bool


@dataclass
class QueueEntry:
    queue_id: str
    url: str
    title: str
    duration: int
    video_id: str
    file_path: str

    def display_text(self) -> str:
        return f"{self.title}  [{format_duration_short(self.duration)}]"


class RingTimeDisplay(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._progress = 0.0
        self._time_text = "00:00:00"

    def sizeHint(self) -> QSize:
        return QSize(220, 220)

    def set_state(self, time_text: str, progress: float) -> None:
        self._time_text = time_text
        self._progress = max(0.0, min(1.0, progress))
        self.update()

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        padding = 14
        rect = self.rect().adjusted(padding, padding, -padding, -padding)

        track_pen = QPen(QColor("#3b3d45"), 12)
        track_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(rect)

        if self._progress > 0.0:
            progress_pen = QPen(QColor("#f27f62"), 12)
            progress_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(progress_pen)
            start_angle = 90 * 16
            span_angle = -int(self._progress * 360 * 16)
            painter.drawArc(rect, start_angle, span_angle)

        painter.setPen(QColor("#d6d7db"))
        font = painter.font()
        font.setPointSize(20)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._time_text)


class TimerCard(QFrame):
    changed = Signal()
    delete_requested = Signal(str)

    def __init__(self, snapshot: TimerSnapshot, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.timer_id = snapshot.timer_id
        self.title = snapshot.title
        self.total_seconds = max(1, int(snapshot.total_seconds))
        self.remaining_seconds = max(0, int(snapshot.remaining_seconds))
        self.is_running = bool(snapshot.is_running)

        self.setObjectName("TimerCard")
        self.setMinimumSize(320, 320)
        self.setMaximumWidth(340)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 12)
        outer.setSpacing(8)

        top = QHBoxLayout()
        self.title_label = QLabel(self.title)
        self.title_label.setStyleSheet("color:#f0f2f5; font-size:20px; font-weight:600;")
        top.addWidget(self.title_label)
        top.addStretch(1)

        self.delete_button = QPushButton("X")
        self.delete_button.setFixedSize(26, 26)
        self.delete_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_button.setToolTip("Delete timer")
        self.delete_button.clicked.connect(lambda: self.delete_requested.emit(self.timer_id))
        top.addWidget(self.delete_button)
        outer.addLayout(top)

        self.ring = RingTimeDisplay()
        outer.addWidget(self.ring, alignment=Qt.AlignmentFlag.AlignCenter)

        controls = QHBoxLayout()
        controls.addStretch(1)

        self.play_pause_button = QPushButton("Play")
        self.play_pause_button.setFixedHeight(34)
        self.play_pause_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_pause_button.clicked.connect(self.toggle_start_pause)
        controls.addWidget(self.play_pause_button)

        self.reset_button = QPushButton("Reset")
        self.reset_button.setFixedHeight(34)
        self.reset_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_button.clicked.connect(self.reset_timer)
        controls.addWidget(self.reset_button)

        controls.addStretch(1)
        outer.addLayout(controls)

        set_row = QHBoxLayout()
        self.hours_spin = QSpinBox()
        self.hours_spin.setRange(0, 99)
        self.hours_spin.setSuffix("h")
        set_row.addWidget(self.hours_spin)

        self.minutes_spin = QSpinBox()
        self.minutes_spin.setRange(0, 59)
        self.minutes_spin.setSuffix("m")
        set_row.addWidget(self.minutes_spin)

        self.seconds_spin = QSpinBox()
        self.seconds_spin.setRange(0, 59)
        self.seconds_spin.setSuffix("s")
        set_row.addWidget(self.seconds_spin)

        self.set_button = QPushButton("Set")
        self.set_button.clicked.connect(self.apply_new_duration)
        set_row.addWidget(self.set_button)
        outer.addLayout(set_row)

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.on_tick)

        self.sync_inputs_from_total()
        self.update_visual_state()
        self.set_running(self.is_running and self.remaining_seconds > 0)

    def sync_inputs_from_total(self) -> None:
        h, rem = divmod(self.total_seconds, 3600)
        m, s = divmod(rem, 60)
        self.hours_spin.setValue(h)
        self.minutes_spin.setValue(m)
        self.seconds_spin.setValue(s)

    def set_running(self, running: bool) -> None:
        self.is_running = bool(running and self.remaining_seconds > 0)
        if self.is_running:
            self.timer.start()
        else:
            self.timer.stop()
        self.play_pause_button.setText("Pause" if self.is_running else "Play")

    def update_visual_state(self) -> None:
        progress = 1.0 - (self.remaining_seconds / self.total_seconds) if self.total_seconds > 0 else 0.0
        self.ring.set_state(format_hms(self.remaining_seconds), progress)

    def apply_new_duration(self) -> None:
        new_total = (
            int(self.hours_spin.value()) * 3600
            + int(self.minutes_spin.value()) * 60
            + int(self.seconds_spin.value())
        )
        self.total_seconds = max(1, new_total)
        self.remaining_seconds = self.total_seconds
        self.set_running(False)
        self.update_visual_state()
        self.changed.emit()

    def toggle_start_pause(self) -> None:
        if self.remaining_seconds <= 0:
            self.remaining_seconds = self.total_seconds
        self.set_running(not self.is_running)
        self.update_visual_state()
        self.changed.emit()

    def reset_timer(self) -> None:
        self.remaining_seconds = self.total_seconds
        self.set_running(False)
        self.update_visual_state()
        self.changed.emit()

    def on_tick(self) -> None:
        if self.remaining_seconds <= 0:
            self.remaining_seconds = 0
            self.set_running(False)
            self.update_visual_state()
            self.changed.emit()
            return

        self.remaining_seconds -= 1
        if self.remaining_seconds <= 0:
            self.remaining_seconds = 0
            self.set_running(False)
        self.update_visual_state()
        self.changed.emit()

    def snapshot(self) -> TimerSnapshot:
        return TimerSnapshot(
            timer_id=self.timer_id,
            title=self.title,
            total_seconds=int(self.total_seconds),
            remaining_seconds=int(self.remaining_seconds),
            is_running=bool(self.is_running),
        )


class YtDownloadWorker(QObject):
    progress = Signal(str)
    finished = Signal(object)
    error = Signal(str)

    def __init__(self, url: str, cache_dir: Path) -> None:
        super().__init__()
        self.url = url
        self.cache_dir = cache_dir

    def run(self) -> None:
        if yt_dlp is None:
            self.error.emit("yt-dlp is not installed. Install with: uv add yt-dlp")
            return

        try:
            canonical_url = canonical_watch_url(self.url)
            video_id = extract_video_id(canonical_url)
            if not video_id:
                self.error.emit("Invalid YouTube URL.")
                return

            self.progress.emit("Fetching metadata...")
            info_opts = {
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "skip_download": True,
            }
            with yt_dlp.YoutubeDL(info_opts) as ydl:
                info = ydl.extract_info(canonical_url, download=False)

            if not isinstance(info, dict):
                self.error.emit("Failed to read video metadata.")
                return

            title = str(info.get("title") or "Untitled")
            duration = int(info.get("duration") or 0)
            video_id = str(info.get("id") or video_id)

            cleanup_partial_cache_files(self.cache_dir, video_id)
            cached = find_cached_audio(self.cache_dir, video_id)
            if cached:
                entry = QueueEntry(
                    queue_id=uuid.uuid4().hex,
                    url=canonical_url,
                    title=title,
                    duration=duration,
                    video_id=video_id,
                    file_path=str(cached),
                )
                self.finished.emit(entry)
                return

            self.progress.emit("Downloading audio...")
            outtmpl = str(self.cache_dir / f"{video_id}.%(ext)s")
            dl_opts = {
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "format": "bestaudio/best",
                "outtmpl": outtmpl,
                "nopart": True,
                "retries": 3,
            }
            with yt_dlp.YoutubeDL(dl_opts) as ydl:
                downloaded_info = ydl.extract_info(canonical_url, download=True)
                downloaded_path = Path(ydl.prepare_filename(downloaded_info)).resolve()

            if not downloaded_path.exists():
                fallback = find_cached_audio(self.cache_dir, video_id)
                if fallback:
                    downloaded_path = fallback
                else:
                    self.error.emit("Download finished but no audio file found in cache.")
                    return

            entry = QueueEntry(
                queue_id=uuid.uuid4().hex,
                url=canonical_url,
                title=title,
                duration=duration,
                video_id=video_id,
                file_path=str(downloaded_path),
            )
            self.finished.emit(entry)
        except Exception as exc:
            self.error.emit(str(exc))

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Timer Dashboard + YouTube Audio")
        self.resize(1420, 900)

        self.base_dir = Path(__file__).resolve().parent
        self.layout_path = self.base_dir / "layout.json"
        self.cache_dir = self.base_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cleanup_all_partial_cache_files(self.cache_dir)

        self.cards: dict[str, TimerCard] = {}
        self.card_order: list[str] = []

        self.active_workers: list[YtDownloadWorker] = []
        self.active_threads: list[QThread] = []

        self.media_player: QMediaPlayer | None = None
        self.audio_output: QAudioOutput | None = None
        self.current_queue_id: str | None = None
        self.is_user_seeking = False

        self.save_timer = QTimer(self)
        self.save_timer.setInterval(250)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self.save_layout)

        self.playback_timer = QTimer(self)
        self.playback_timer.setInterval(500)
        self.playback_timer.timeout.connect(self.update_playback_progress)
        self.playback_timer.start()

        self._build_ui()
        self._init_audio_player()
        self.load_layout()

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(14, 14, 14, 14)
        root_layout.setSpacing(10)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root_layout.addWidget(splitter)

        # Left side: timer dashboard
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        top = QHBoxLayout()
        self.add_1m = QPushButton("1 min")
        self.add_1m.clicked.connect(lambda: self.add_timer_card("1 min", 60))
        top.addWidget(self.add_1m)

        self.add_3m = QPushButton("3 min")
        self.add_3m.clicked.connect(lambda: self.add_timer_card("3 min", 180))
        top.addWidget(self.add_3m)

        self.add_1h = QPushButton("1 hour")
        self.add_1h.clicked.connect(lambda: self.add_timer_card("1 hour", 3600))
        top.addWidget(self.add_1h)

        self.custom_title = QLineEdit()
        self.custom_title.setPlaceholderText("Timer name")
        self.custom_title.setFixedWidth(180)
        top.addWidget(self.custom_title)

        self.custom_h = QSpinBox()
        self.custom_h.setRange(0, 99)
        self.custom_h.setSuffix("h")
        top.addWidget(self.custom_h)

        self.custom_m = QSpinBox()
        self.custom_m.setRange(0, 59)
        self.custom_m.setSuffix("m")
        top.addWidget(self.custom_m)

        self.custom_s = QSpinBox()
        self.custom_s.setRange(0, 59)
        self.custom_s.setSuffix("s")
        top.addWidget(self.custom_s)

        self.add_custom = QPushButton("Add Timer")
        self.add_custom.clicked.connect(self.add_custom_timer)
        top.addWidget(self.add_custom)

        top.addStretch(1)
        left_layout.addLayout(top)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.cards_container = QWidget()
        self.cards_layout = QGridLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setHorizontalSpacing(14)
        self.cards_layout.setVerticalSpacing(14)

        self.scroll.setWidget(self.cards_container)
        left_layout.addWidget(self.scroll)

        splitter.addWidget(left)

        # Right side: YouTube audio panel
        right = QWidget()
        right.setObjectName("MusicPanel")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(10, 10, 10, 10)
        right_layout.setSpacing(8)

        input_row = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Paste YouTube URL...")
        input_row.addWidget(self.url_input)

        self.add_url_button = QPushButton("Add")
        self.add_url_button.clicked.connect(self.handle_add_url)
        input_row.addWidget(self.add_url_button)
        right_layout.addLayout(input_row)

        self.stack_list = QListWidget()
        self.stack_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.stack_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.stack_list.itemDoubleClicked.connect(self.play_selected_item)
        right_layout.addWidget(self.stack_list, 1)

        queue_buttons = QHBoxLayout()
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove_selected_item)
        queue_buttons.addWidget(self.remove_button)

        self.up_button = QPushButton("Up")
        self.up_button.clicked.connect(lambda: self.move_selected_item(-1))
        queue_buttons.addWidget(self.up_button)

        self.down_button = QPushButton("Down")
        self.down_button.clicked.connect(lambda: self.move_selected_item(1))
        queue_buttons.addWidget(self.down_button)
        right_layout.addLayout(queue_buttons)

        player_buttons = QHBoxLayout()
        self.prev_button = QPushButton("Prev")
        self.prev_button.clicked.connect(self.play_previous)
        player_buttons.addWidget(self.prev_button)

        self.play_pause_button = QPushButton("Play")
        self.play_pause_button.clicked.connect(self.toggle_play_pause)
        player_buttons.addWidget(self.play_pause_button)

        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_playback)
        player_buttons.addWidget(self.stop_button)

        self.next_button = QPushButton("Next")
        self.next_button.clicked.connect(self.play_next)
        player_buttons.addWidget(self.next_button)
        right_layout.addLayout(player_buttons)

        self.now_playing_label = QLabel("Now playing: (none)")
        self.now_playing_label.setWordWrap(True)
        right_layout.addWidget(self.now_playing_label)

        progress_row = QHBoxLayout()
        self.progress_label = QLabel("00:00 / 00:00")
        self.progress_label.setMinimumWidth(110)
        progress_row.addWidget(self.progress_label)

        self.progress_slider = QSlider(Qt.Orientation.Horizontal)
        self.progress_slider.setRange(0, 1000)
        self.progress_slider.sliderPressed.connect(self.on_progress_slider_pressed)
        self.progress_slider.sliderReleased.connect(self.on_progress_slider_released)
        progress_row.addWidget(self.progress_slider, 1)
        right_layout.addLayout(progress_row)

        volume_row = QHBoxLayout()
        volume_row.addWidget(QLabel("Volume"))
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.valueChanged.connect(self.on_volume_changed)
        volume_row.addWidget(self.volume_slider, 1)
        right_layout.addLayout(volume_row)

        self.music_status = QLabel("Ready.")
        self.music_status.setWordWrap(True)
        right_layout.addWidget(self.music_status)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        self.setCentralWidget(root)
        self.apply_styles()

    def apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #1f2128;
                color: #e7e8eb;
            }
            QPushButton {
                background: #2d3038;
                border: 1px solid #3a3e47;
                border-radius: 8px;
                padding: 6px 10px;
                color: #eceef1;
            }
            QPushButton:hover {
                background: #343843;
            }
            QLineEdit, QSpinBox, QListWidget {
                background: #2a2d36;
                border: 1px solid #3a3e47;
                border-radius: 8px;
                padding: 5px 8px;
                color: #e9ebee;
            }
            QFrame#TimerCard {
                background: #2a2c33;
                border: 1px solid #3a3d45;
                border-radius: 12px;
            }
            QFrame#TimerCard QPushButton {
                background: #3a3d45;
                border: 1px solid #4a4d56;
            }
            QFrame#TimerCard QPushButton:hover {
                background: #444852;
            }
            QWidget#MusicPanel {
                background: #252830;
                border: 1px solid #3a3e47;
                border-radius: 10px;
            }
            """
        )

        for card in self.cards.values():
            card.play_pause_button.setStyleSheet(
                "background:#f27f62; border:1px solid #f27f62; color:#101316; border-radius:8px;"
            )

    def resizeEvent(self, event: Any) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.relayout_cards()

    # ----- Timer dashboard -----
    def add_custom_timer(self) -> None:
        total = int(self.custom_h.value()) * 3600 + int(self.custom_m.value()) * 60 + int(self.custom_s.value())
        if total <= 0:
            QMessageBox.information(self, "Timer", "Set a duration greater than 0.")
            return

        title = self.custom_title.text().strip() or "Timer"
        self.add_timer_card(title, total)

    def add_timer_card(
        self,
        title: str,
        total_seconds: int,
        timer_id: str | None = None,
        remaining_seconds: int | None = None,
        is_running: bool = False,
        save: bool = True,
    ) -> None:
        timer_id = timer_id or uuid.uuid4().hex
        if timer_id in self.cards:
            return

        snapshot = TimerSnapshot(
            timer_id=timer_id,
            title=title,
            total_seconds=max(1, int(total_seconds)),
            remaining_seconds=max(0, int(remaining_seconds if remaining_seconds is not None else total_seconds)),
            is_running=bool(is_running),
        )

        card = TimerCard(snapshot)
        card.changed.connect(self.queue_save)
        card.delete_requested.connect(self.delete_timer_card)
        card.play_pause_button.setStyleSheet(
            "background:#f27f62; border:1px solid #f27f62; color:#101316; border-radius:8px;"
        )

        self.cards[timer_id] = card
        self.card_order.append(timer_id)

        self.relayout_cards()
        if save:
            self.queue_save()

    def delete_timer_card(self, timer_id: str) -> None:
        card = self.cards.pop(timer_id, None)
        if card is None:
            return

        if timer_id in self.card_order:
            self.card_order.remove(timer_id)

        self.cards_layout.removeWidget(card)
        card.deleteLater()

        self.relayout_cards()
        self.queue_save()

    def relayout_cards(self) -> None:
        while self.cards_layout.count() > 0:
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(self.cards_container)

        viewport_width = max(320, self.scroll.viewport().width())
        col_width = 340
        cols = max(1, viewport_width // col_width)

        row = 0
        col = 0
        for timer_id in self.card_order:
            card = self.cards.get(timer_id)
            if card is None:
                continue
            self.cards_layout.addWidget(card, row, col)
            col += 1
            if col >= cols:
                col = 0
                row += 1

        self.cards_layout.setColumnStretch(cols, 1)
        self.cards_layout.setRowStretch(row + 1, 1)

    # ----- YouTube audio -----
    def _set_music_status(self, message: str) -> None:
        self.music_status.setText(message)

    def _set_music_controls_enabled(self, enabled: bool) -> None:
        controls = [
            self.add_url_button,
            self.remove_button,
            self.up_button,
            self.down_button,
            self.prev_button,
            self.play_pause_button,
            self.stop_button,
            self.next_button,
            self.progress_slider,
            self.volume_slider,
        ]
        for control in controls:
            control.setEnabled(enabled)

    def _init_audio_player(self) -> None:
        if QMediaPlayer is None or QAudioOutput is None:
            self._set_music_controls_enabled(False)
            self._set_music_status("Audio backend unavailable in this PySide6 build.")
            return

        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(self.volume_slider.value() / 100.0)

        self.media_player = QMediaPlayer(self)
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.media_player.errorOccurred.connect(self._on_media_error)

        self._set_music_status("Ready.")

    def _on_media_error(self, _error: Any, error_string: str) -> None:
        if error_string:
            self._set_music_status(f"Playback error: {error_string}")

    def _on_media_status_changed(self, status: Any) -> None:
        if QMediaPlayer is None:
            return
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.play_next(auto_triggered=True)

    def handle_add_url(self) -> None:
        if yt_dlp is None:
            self._set_music_status("yt-dlp not installed. Run: uv add yt-dlp")
            return

        url = self.url_input.text().strip()
        if not url:
            self._set_music_status("Paste a YouTube URL.")
            return
        if not is_valid_youtube_url(url):
            self._set_music_status("Invalid YouTube URL.")
            return

        worker = YtDownloadWorker(url=url, cache_dir=self.cache_dir)
        thread = QThread(self)
        worker.moveToThread(thread)

        worker.progress.connect(self._set_music_status)
        worker.finished.connect(self.on_download_finished)
        worker.error.connect(self.on_download_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.started.connect(worker.run)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._cleanup_worker_thread(worker, thread))

        self.active_workers.append(worker)
        self.active_threads.append(thread)

        self._set_music_status("Fetching metadata/download...")
        thread.start()

    def _cleanup_worker_thread(self, worker: YtDownloadWorker, thread: QThread) -> None:
        if worker in self.active_workers:
            self.active_workers.remove(worker)
        if thread in self.active_threads:
            self.active_threads.remove(thread)

    def on_download_finished(self, entry: QueueEntry) -> None:
        item = QListWidgetItem(entry.display_text())
        item.setData(Qt.ItemDataRole.UserRole, entry)
        self.stack_list.addItem(item)
        self.url_input.clear()
        self._set_music_status(f"Added: {entry.title}")

        if self.stack_list.count() == 1:
            self.stack_list.setCurrentRow(0)

    def on_download_error(self, message: str) -> None:
        self._set_music_status(f"Error: {message}")

    def selected_or_first_row(self) -> int:
        row = self.stack_list.currentRow()
        if row < 0 and self.stack_list.count() > 0:
            return 0
        return row

    def find_current_row(self) -> int:
        if self.current_queue_id:
            for row in range(self.stack_list.count()):
                item = self.stack_list.item(row)
                entry = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(entry, QueueEntry) and entry.queue_id == self.current_queue_id:
                    return row
        return self.selected_or_first_row()

    def play_selected_item(self, *_args: Any) -> None:
        self.play_row(self.selected_or_first_row())

    def play_row(self, row: int) -> None:
        if self.media_player is None:
            self._set_music_status("Audio player unavailable.")
            return
        if row < 0 or row >= self.stack_list.count():
            self._set_music_status("Select an item to play.")
            return

        item = self.stack_list.item(row)
        entry = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(entry, QueueEntry):
            self._set_music_status("Invalid queue entry.")
            return

        file_path = Path(entry.file_path)
        if not file_path.exists():
            self._set_music_status("Cached file missing. Remove and add URL again.")
            return

        self.media_player.setSource(QUrl.fromLocalFile(str(file_path)))
        self.media_player.play()

        self.current_queue_id = entry.queue_id
        self.stack_list.setCurrentRow(row)
        self.play_pause_button.setText("Pause")
        self.now_playing_label.setText(f"Now playing: {entry.title}")
        self._set_music_status(f"Playing: {entry.title}")

    def toggle_play_pause(self) -> None:
        if self.media_player is None or QMediaPlayer is None:
            self._set_music_status("Audio player unavailable.")
            return

        state = self.media_player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
            self.play_pause_button.setText("Play")
            return

        if self.current_queue_id is None:
            self.play_selected_item()
        else:
            self.media_player.play()
            self.play_pause_button.setText("Pause")

    def stop_playback(self) -> None:
        if self.media_player is None:
            return
        self.media_player.stop()
        self.play_pause_button.setText("Play")
        self.progress_slider.setValue(0)
        self.progress_label.setText("00:00 / 00:00")
        self.now_playing_label.setText("Now playing: (none)")
        self.current_queue_id = None
        self._set_music_status("Stopped.")

    def play_next(self, auto_triggered: bool = False) -> None:
        current_row = self.find_current_row()
        if current_row < 0:
            self._set_music_status("Queue is empty.")
            return

        next_row = current_row + 1
        if next_row >= self.stack_list.count():
            self.play_pause_button.setText("Play")
            if auto_triggered:
                self._set_music_status("Reached end of queue.")
            return

        self.play_row(next_row)

    def play_previous(self) -> None:
        current_row = self.find_current_row()
        if current_row <= 0:
            self._set_music_status("No previous item.")
            return
        self.play_row(current_row - 1)

    def remove_selected_item(self) -> None:
        row = self.stack_list.currentRow()
        if row < 0:
            self._set_music_status("Select an item to remove.")
            return

        item = self.stack_list.item(row)
        entry = item.data(Qt.ItemDataRole.UserRole)
        was_current = isinstance(entry, QueueEntry) and entry.queue_id == self.current_queue_id

        self.stack_list.takeItem(row)
        if was_current:
            self.stop_playback()
        self._set_music_status("Removed item.")

    def move_selected_item(self, direction: int) -> None:
        row = self.stack_list.currentRow()
        if row < 0:
            self._set_music_status("Select an item to reorder.")
            return

        new_row = row + direction
        if new_row < 0 or new_row >= self.stack_list.count():
            return

        item = self.stack_list.takeItem(row)
        self.stack_list.insertItem(new_row, item)
        self.stack_list.setCurrentRow(new_row)
        self._set_music_status("Reordered queue.")

    def on_volume_changed(self, value: int) -> None:
        if self.audio_output is not None:
            self.audio_output.setVolume(value / 100.0)

    def on_progress_slider_pressed(self) -> None:
        self.is_user_seeking = True

    def on_progress_slider_released(self) -> None:
        self.is_user_seeking = False
        if self.media_player is None:
            return

        duration = self.media_player.duration()
        if duration and duration > 0:
            new_position = int((self.progress_slider.value() / 1000.0) * duration)
            self.media_player.setPosition(new_position)

    def update_playback_progress(self) -> None:
        if self.media_player is None:
            return

        duration = self.media_player.duration()
        position = self.media_player.position()
        if duration and duration > 0 and position >= 0:
            if not self.is_user_seeking:
                value = int((position / duration) * 1000)
                self.progress_slider.blockSignals(True)
                self.progress_slider.setValue(max(0, min(1000, value)))
                self.progress_slider.blockSignals(False)

            elapsed = format_duration_short(int(position / 1000))
            total = format_duration_short(int(duration / 1000))
            self.progress_label.setText(f"{elapsed} / {total}")

    # ----- Persistence -----
    def queue_save(self) -> None:
        self.save_timer.start()

    def save_layout(self) -> None:
        payload = {
            "timers": [asdict(self.cards[timer_id].snapshot()) for timer_id in self.card_order if timer_id in self.cards],
        }
        try:
            self.layout_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            QMessageBox.warning(self, "Save Error", f"Could not save layout.json\n\n{exc}")

    def load_layout(self) -> None:
        data: dict[str, Any] = {}
        if self.layout_path.exists():
            try:
                raw = self.layout_path.read_text(encoding="utf-8")
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    data = parsed
            except Exception:
                data = {}

        timers_data = data.get("timers")
        if isinstance(timers_data, list) and timers_data:
            loaded = False
            for item in timers_data:
                if not isinstance(item, dict):
                    continue
                timer_id = str(item.get("timer_id") or uuid.uuid4().hex)
                title = str(item.get("title") or "Timer")
                total = int(item.get("total_seconds") or 60)
                remaining = int(item.get("remaining_seconds") or total)
                is_running = bool(item.get("is_running", False))
                self.add_timer_card(
                    title=title,
                    total_seconds=total,
                    timer_id=timer_id,
                    remaining_seconds=remaining,
                    is_running=is_running,
                    save=False,
                )
                loaded = True

            if loaded:
                self.relayout_cards()
                return

        self.add_timer_card("1 min", 60, save=False)
        self.add_timer_card("3 min", 180, save=False)
        self.add_timer_card("1 hour", 3600, save=False)
        self.save_layout()

    def closeEvent(self, event: Any) -> None:  # noqa: N802
        try:
            self.save_layout()
            if self.media_player is not None:
                self.media_player.stop()
        except Exception:
            pass
        super().closeEvent(event)


def maybe_rebuild_executable() -> None:
    # Skip build when running from a packaged executable.
    if getattr(sys, "frozen", False):
        return

    if "--skip-build" in sys.argv:
        sys.argv[:] = [arg for arg in sys.argv if arg != "--skip-build"]
        return

    try:
        import PyInstaller.__main__  # type: ignore # noqa: F401
    except Exception:
        print("PyInstaller is not installed. Run: uv add pyinstaller")
        return

    base_dir = Path(__file__).resolve().parent
    script_path = base_dir / "main.py"
    dist_dir = base_dir / "dist"
    build_dir = base_dir / "build"
    spec_path = base_dir / f"{EXE_BASE_NAME}.spec"
    exe_name = f"{EXE_BASE_NAME}.exe" if os.name == "nt" else EXE_BASE_NAME
    exe_path = dist_dir / exe_name

    dist_dir.mkdir(parents=True, exist_ok=True)

    if exe_path.exists():
        try:
            exe_path.unlink()
            print(f"Removed old executable: {exe_path}")
        except Exception as exc:
            print(f"Could not remove old executable ({exe_path}): {exc}")
            return

    if build_dir.exists():
        shutil.rmtree(build_dir, ignore_errors=True)
    if spec_path.exists():
        try:
            spec_path.unlink()
        except Exception:
            pass

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--windowed",
        "--onefile",
        "--name",
        EXE_BASE_NAME,
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(build_dir),
        "--specpath",
        str(base_dir),
        str(script_path),
    ]

    print("Building executable...")
    result = subprocess.run(command, cwd=str(base_dir), check=False)
    if result.returncode == 0:
        print(f"Built executable: {exe_path}")
    else:
        print("Executable build failed.")


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    maybe_rebuild_executable()
    raise SystemExit(main())
