"""
Timer Dashboard (no canvas)

A normal timer app styled like a card grid:
- multiple timer cards
- each card has countdown ring, start/pause, reset
- editable duration per card
- add/remove timers
- layout/state persistence to layout.json

Run:
    uv run python main.py
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


def format_hms(total_seconds: int) -> str:
    total_seconds = max(0, int(total_seconds))
    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


@dataclass
class TimerSnapshot:
    timer_id: str
    title: str
    total_seconds: int
    remaining_seconds: int
    is_running: bool


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

        # Track ring
        track_pen = QPen(QColor("#3b3d45"), 12)
        track_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(rect)

        # Progress ring
        if self._progress > 0.0:
            progress_pen = QPen(QColor("#f27f62"), 12)
            progress_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(progress_pen)
            start_angle = 90 * 16
            span_angle = -int(self._progress * 360 * 16)
            painter.drawArc(rect, start_angle, span_angle)

        # Time text
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

        self.delete_button = QPushButton("✕")
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

        self.play_pause_button = QPushButton("▶")
        self.play_pause_button.setFixedSize(36, 36)
        self.play_pause_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_pause_button.clicked.connect(self.toggle_start_pause)
        controls.addWidget(self.play_pause_button)

        self.reset_button = QPushButton("↻")
        self.reset_button.setFixedSize(32, 32)
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
        self.play_pause_button.setText("⏸" if self.is_running else "▶")

    def update_visual_state(self) -> None:
        if self.total_seconds <= 0:
            progress = 0.0
        else:
            progress = 1.0 - (self.remaining_seconds / self.total_seconds)
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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Timer Dashboard")
        self.resize(1320, 860)

        self.base_dir = Path(__file__).resolve().parent
        self.layout_path = self.base_dir / "layout.json"

        self.cards: dict[str, TimerCard] = {}
        self.card_order: list[str] = []

        self.save_timer = QTimer(self)
        self.save_timer.setInterval(250)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self.save_layout)

        self._build_ui()
        self.load_layout()

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(14, 14, 14, 14)
        root_layout.setSpacing(10)

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
        root_layout.addLayout(top)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.cards_container = QWidget()
        self.cards_layout = QGridLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setHorizontalSpacing(14)
        self.cards_layout.setVerticalSpacing(14)

        self.scroll.setWidget(self.cards_container)
        root_layout.addWidget(self.scroll)

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
            QLineEdit, QSpinBox {
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
            """
        )

        # Accent buttons in cards
        for card in self.cards.values():
            card.play_pause_button.setStyleSheet(
                "background:#f27f62; border:1px solid #f27f62; color:#101316; border-radius:18px;"
            )

    def resizeEvent(self, event: Any) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.relayout_cards()

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
            "background:#f27f62; border:1px solid #f27f62; color:#101316; border-radius:18px;"
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

        # Default cards when there is no compatible timer layout.
        self.add_timer_card("1 min", 60, save=False)
        self.add_timer_card("3 min", 180, save=False)
        self.add_timer_card("1 hour", 3600, save=False)
        self.save_layout()


def main() -> int:
    app = QApplication([])
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
