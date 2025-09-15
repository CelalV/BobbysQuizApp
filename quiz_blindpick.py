# quiz_blindpick.py
# Implementiert:
# - SetupDialog: Spielernamen + Template-JSON laden (rounds [{title, video, truth}])
# - AudienceWindow: Video oben, mittig Antworten als gerahmte Zeilen mit rechts ausgerichteten, NON-interaktiven Auswahlspalten, unten quadratische Kamera-/Score-Overlays
# - ControlWindow: Moderatorsteuerung mit Eingabe, Mischen & Anzeigen, ButtonGroup-Single-Choice, gezieltem Aufdecken (Button wird grün/"Aufgedeckt"), Punktevergabe
# - Lautstärke-Slider im Moderationsfenster (steuert QAudioOutput des Zuschauerfensters)
# - BlindPickQuiz: Wrapper mit rückwärtskompatibler __init__

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Optional
from collections import deque
import random

from PySide6.QtCore import Qt, QUrl, QSize
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton,
    QGridLayout, QGroupBox, QSpacerItem, QSizePolicy, QDialog, QPlainTextEdit,
    QFileDialog, QLineEdit, QMessageBox, QButtonGroup, QScrollArea, QCheckBox, QFrame, QSlider
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

# =========================
# Konfiguration (anpassen)
# =========================
SQUARE_MAX = 220          # maximale Kantenlänge der Kamera-Slots (px) — hier anpassen
ANSWER_ROW_PADDING = 10   # Innenabstand in jeder Antwort-Zeile (px) — hier anpassen
ROW_SPACING = 8           # vertikaler Abstand zwischen Antwort-Zeilen (px) — hier anpassen
COL_SPACING = 200         # horizontaler Abstand zwischen Spalten (px) — hier anpassen
CHK_PADDING = 1           # Padding pro Checkboxzelle (px) — hier anpassen
BTN_COLOR_SHOW = "#0d6efd"  # hellblau: "Aufdecken" — hier anpassen
BTN_COLOR_DONE = "#2e7d32"  # grün: "Aufgedeckt" — hier anpassen
DEFAULT_VOLUME = 70       # Startlautstärke in Prozent — hier anpassen
# =========================

# ------------------------
# Datenstrukturen
# ------------------------

@dataclass
class RoundTemplate:
    title: str
    video: str
    truth: str

@dataclass
class RoundRuntime:
    players_answers: List[str] = field(default_factory=list)            # Länge = len(players)
    shuffled_order: List[int] = field(default_factory=list)             # Permutation über range(len(players)+1)
    revealed: List[bool] = field(default_factory=list)                  # Sichtbarkeit pro Zeile (Anzeige-Reihenfolge)
    selections: Dict[str, Optional[int]] = field(default_factory=dict)  # player -> Zeilenindex (Anzeige-Reihenfolge)

# ------------------------
# Setup-Dialog
# ------------------------

class SetupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Blind Pick — Setup")
        self.resize(600, 500)
        v = QVBoxLayout(self)

        v.addWidget(QLabel("Spielernamen (je Zeile ein Name):"))
        self.players_edit = QPlainTextEdit()
        self.players_edit.setPlaceholderText("Player1\nPlayer2\nPlayer3")
        v.addWidget(self.players_edit, 1)

        hv = QHBoxLayout()
        self.template_edit = QLineEdit()
        self.template_edit.setPlaceholderText("Pfad zum Template-JSON (Runden)")
        self.btn_browse = QPushButton("Template laden …")
        self.btn_browse.clicked.connect(self.choose_template)
        hv.addWidget(self.template_edit, 1)
        hv.addWidget(self.btn_browse)
        v.addLayout(hv)

        hb = QHBoxLayout()
        self.btn_cancel = QPushButton("Abbrechen")
        self.btn_ok = QPushButton("Starten")
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self.accept)
        hb.addStretch(1)
        hb.addWidget(self.btn_cancel)
        hb.addWidget(self.btn_ok)
        v.addLayout(hb)

        self.players: List[str] = []
        self.template: List[RoundTemplate] = []

    def choose_template(self):
        path, _ = QFileDialog.getOpenFileName(self, "Template öffnen", "", "JSON (*.json)")
        if not path:
            return
        self.template_edit.setText(path)

    def accept(self):
        raw = self.players_edit.toPlainText().splitlines()
        players = [x.strip() for x in raw if x.strip()]
        if len(players) < 2:
            QMessageBox.information(self, "Hinweis", "Bitte mindestens 2 Spielernamen eingeben.")
            return
        path = self.template_edit.text().strip()
        if not path:
            QMessageBox.information(self, "Hinweis", "Bitte ein Template-JSON wählen.")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Konnte Template nicht laden:\n{e}")
            return
        rounds = data.get("rounds", [])
        if not isinstance(rounds, list) or not rounds:
            QMessageBox.information(self, "Hinweis", "Das Template enthält keine Runden.")
            return
        templ: List[RoundTemplate] = []
        for i, r in enumerate(rounds, start=1):
            title = r.get("title") or f"Runde {i}"
            video = r.get("video") or ""
            # Hinweis: Dieses Feld heißt im Editor i.d.R. "truth"; falls dort "Richtige Antwort" verwendet wird, bitte angleichen.
            truth = r.get("Richtige Antwort") or r.get("truth") or ""
            templ.append(RoundTemplate(title=title, video=video, truth=truth))
        self.players = players
        self.template = templ
        super().accept()

# ------------------------
# Hilfs-Widgets
# ------------------------

class SquareWidget(QWidget):
    def sizeHint(self) -> QSize:
        return QSize(min(240, SQUARE_MAX), min(240, SQUARE_MAX))
    def hasHeightForWidth(self) -> bool:
        return True
    def heightForWidth(self, w: int) -> int:
        return min(w, SQUARE_MAX)

def style_button(btn: QPushButton, color: str):
    btn.setStyleSheet(
        f"QPushButton {{ background-color: {color}; color: white; border: none; padding: 6px 10px; border-radius: 6px; }}"
    )

# ------------------------
# Zuschauerfenster
# ------------------------

class AudienceWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Blind Pick — Zuschauer")
        self.resize(2043, 1392)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # Globaler Vorbereitungshinweis (nur vor Setup)
        self.prep_label = QLabel("Das Quiz wird vorbereitet …", alignment=Qt.AlignCenter)
        self.prep_label.setStyleSheet("font-size: 28px;")
        root.addWidget(self.prep_label)

        # Video
        self.video_widget = QVideoWidget()
        root.addWidget(self.video_widget, 6)

        # Antwortenbereich (Scroll + Grid)
        self.answers_area = QScrollArea()
        self.answers_area.setWidgetResizable(True)
        self.answers_container = QWidget()
        self.answers_grid = QGridLayout(self.answers_container)
        self.answers_grid.setContentsMargins(0, 0, 0, 0)
        self.answers_grid.setHorizontalSpacing(COL_SPACING)
        self.answers_grid.setVerticalSpacing(ROW_SPACING)
        self.answers_area.setWidget(self.answers_container)
        root.addWidget(self.answers_area, 5)

        # Platzhalter in der Mitte bis zum Mischen
        self.wait_label = QLabel("Warte auf Antworten …", alignment=Qt.AlignCenter)
        self.wait_label.setStyleSheet("font-size: 22px; color: #AAA;")

        # Score/Overlay mit quadratischen Slots
        self.overlay = QWidget()
        self.overlay_grid = QGridLayout(self.overlay)
        root.addWidget(self.overlay, 2)

        # Media
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video_widget)

        # Laufzeit
        self.players: List[str] = []
        self.col_players: List[str] = []
        self.score_labels: Dict[str, QLabel] = {}
        self.sel_boxes: Dict[str, List[QCheckBox]] = {}  # pname -> list[checkbox per row]

        self.set_global_preparing(True)

    # Sichtbarkeit
    def set_global_preparing(self, on: bool):
        self.prep_label.setVisible(on)
        self.video_widget.setVisible(not on)
        self.answers_area.setVisible(not on)
        self.overlay.setVisible(not on)

    def show_waiting_center(self):
        self._clear_answers_grid()
        self.answers_grid.addWidget(self.wait_label, 0, 0, 1, 1, alignment=Qt.AlignCenter)

    # Konfiguration
    def configure_players(self, players: List[str]):
        self.players = list(players)
        for i in reversed(range(self.overlay_grid.count())):
            it = self.overlay_grid.takeAt(i)
            if it.widget():
                it.widget().deleteLater()
        self.score_labels.clear()
        for c, name in enumerate(players):
            box = QGroupBox(name)
            vb = QVBoxLayout(box)
            sq = SquareWidget()
            sq.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
            sq.setMaximumSize(QSize(SQUARE_MAX, SQUARE_MAX))  # Begrenzung
            vb.addWidget(sq)
            score = QLabel("Punkte: 0", alignment=Qt.AlignCenter)
            score.setFont(QFont("", 12, QFont.Bold))
            vb.addWidget(score)
            self.score_labels[name] = score
            self.overlay_grid.addWidget(box, 0, c)

    # Medien
    def set_video(self, path: str):
        url = QUrl.fromLocalFile(str(Path(path).absolute()))
        self.player.setSource(url)

    def play(self): self.player.play()
    def pause(self): self.player.pause()
    def stop(self): self.player.stop()

    # Antwortenraster
    def _clear_answers_grid(self):
        for i in reversed(range(self.answers_grid.count())):
            it = self.answers_grid.takeAt(i)
            if it.widget():
                it.widget().deleteLater()

    def set_answers_grid(self, slots: List[Dict], revealed: List[bool], col_players: List[str], selections: Dict[str, Optional[int]]):
        self._clear_answers_grid()
        self.col_players = list(col_players)

        # Kopfzeile
        hdr_ans = QLabel("Antworten")
        hdr_ans.setStyleSheet("font-weight: bold;font-size: 16px;")
        hdr_ans.setWordWrap(True)
        hdr_ans.setFixedWidth(1000)
        hdr_ans.setContentsMargins(20, 0, 0, 0)
        self.answers_grid.addWidget(hdr_ans, 0, 0)
        for c, pname in enumerate(self.col_players, start=1):
            lab = QLabel(pname); lab.setAlignment(Qt.AlignCenter)
            lab.setStyleSheet("font-weight: bold;font-size: 14px;")
            self.answers_grid.addWidget(lab, 0, c)

        # Checkboxen-Container
        self.sel_boxes = {p: [] for p in self.col_players}

        # Zeilen (je Zeile ein gerahmter Container)
        for r, slot in enumerate(slots, start=1):
            row_frame = QFrame()
            row_frame.setObjectName("answerRow")
            row_frame.setStyleSheet(
                "#answerRow { border: 1px solid #777; border-radius: 8px; background: rgba(255,255,255,0.03); }"
            )
            inner = QGridLayout(row_frame)
            inner.setContentsMargins(ANSWER_ROW_PADDING, ANSWER_ROW_PADDING, ANSWER_ROW_PADDING, ANSWER_ROW_PADDING)
            inner.setHorizontalSpacing(COL_SPACING)
            inner.setVerticalSpacing(0)

            # Linke Zelle: Autor + Text
            author = slot["author"]
            text = slot["text"]
            leftw = QWidget(); lh = QVBoxLayout(leftw); lh.setContentsMargins(0,0,0,0); lh.setSpacing(5)
            left_top = QLabel(f"Autor: {author if revealed[r-1] else '???'}")
            left_top.setStyleSheet("color: #CCC;font-size: 14px;")
            left_body = QLabel(text or "(leer)")
            left_body.setWordWrap(True)
            left_body.setStyleSheet("font-size: 20px;")
            left_body.setFixedWidth(1000)
            lh.addWidget(left_top)
            lh.addWidget(left_body)
            inner.addWidget(leftw, 0, 0)

            # Rechts: Checkbox-Spalten
            for c, pname in enumerate(self.col_players, start=1):
                cell = QWidget(); ch = QHBoxLayout(cell)
                ch.setContentsMargins(CHK_PADDING, 0, CHK_PADDING, 0)
                ch.setAlignment(Qt.AlignCenter)
                cb = QCheckBox()
                cb.setEnabled(False)  # nur Anzeige
                cell.setStyleSheet("QCheckBox { margin: 0px; }")  # Konsistenter Look
                self.sel_boxes[pname].append(cb)
                ch.addWidget(cb, alignment=Qt.AlignCenter)
                inner.addWidget(cell, 0, c, alignment=Qt.AlignCenter)

            # Den Rahmen über alle Spalten des äußeren Grids legen
            self.answers_grid.addWidget(row_frame, r, 0, 1, len(self.col_players)+1)

        # Selektionen nach Neuaufbau wiederherstellen (Persistenz)
        for pname, sel in selections.items():
            self.update_selection(pname, sel)

    def update_selection(self, pname: str, selected_row: Optional[int]):
        boxes = self.sel_boxes.get(pname)
        if not boxes:
            return
        for i, cb in enumerate(boxes):
            cb.setChecked(i == selected_row if selected_row is not None else False)

    def set_scores(self, scores: Dict[str, int]):
        for name, val in scores.items():
            if name in self.score_labels:
                self.score_labels[name].setText(f"Punkte: {val}")

# ------------------------
# Moderatorfenster
# ------------------------

class ControlWindow(QMainWindow):
    def __init__(self, on_close, audience: AudienceWindow):
        super().__init__()
        self.setWindowTitle("Blind Pick — Moderator")
        self.resize(1280, 960)

        self.on_close = on_close
        self.audience = audience

        # Setup-Daten
        self.players: List[str] = []
        self.templates: List[RoundTemplate] = []
        self.scores: Dict[str, int] = {}
        self.round_index: int = 0

        # Laufzeit je Runde
        self.runtime: RoundRuntime = RoundRuntime()

        # UI
        central = QWidget(); self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # Setup / Navigation
        top = QHBoxLayout()
        self.btn_setup = QPushButton("Setup (Spieler + Template) …")
        self.btn_prev = QPushButton("← Runde")
        self.btn_next = QPushButton("Runde →")
        self.lbl_round = QLabel("Runde: –")
        top.addWidget(self.btn_setup)
        top.addStretch(1)
        top.addWidget(self.btn_prev); top.addWidget(self.btn_next); top.addWidget(self.lbl_round)
        root.addLayout(top)

        # Video-Steuerung + Lautstärke
        vid = QHBoxLayout()
        self.btn_play = QPushButton("Play")
        self.btn_pause = QPushButton("Pause")
        self.btn_stop = QPushButton("Stop")
        vid.addWidget(self.btn_play); vid.addWidget(self.btn_pause); vid.addWidget(self.btn_stop)

        vid.addSpacing(20)
        vid.addWidget(QLabel("Lautstärke:"))
        self.vol_slider = QSlider(Qt.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(DEFAULT_VOLUME)
        self.vol_slider.setFixedWidth(180)
        self.vol_label = QLabel(f"{DEFAULT_VOLUME}%")
        vid.addWidget(self.vol_slider)
        vid.addWidget(self.vol_label)
        root.addLayout(vid)

        # Antwort-Eingaben
        self.answers_group = QGroupBox("Antworten eingeben (für aktuelle Runde)")
        agl = QGridLayout(self.answers_group)
        self.answer_edits: Dict[str, QLineEdit] = {}
        root.addWidget(self.answers_group)

        # Mischen & Anzeigen
        ctl = QHBoxLayout()
        self.btn_shuffle = QPushButton("Mischen & anzeigen")
        ctl.addWidget(self.btn_shuffle)
        root.addLayout(ctl)

        # Checkbox-Raster + per-Zeile-Aufdecken
        self.chk_group = QGroupBox("Auswahl je Spieler (Autor + Antwort links, Spalten rotiert)")
        self.chk_grid = QGridLayout(self.chk_group)
        self.chk_grid.setHorizontalSpacing(5)
        self.chk_grid.setVerticalSpacing(5)
        root.addWidget(self.chk_group)

        # Signale
        self.btn_setup.clicked.connect(self.run_setup)
        self.btn_prev.clicked.connect(self.prev_round)
        self.btn_next.clicked.connect(self.next_round)
        self.btn_play.clicked.connect(self.audience.play)
        self.btn_pause.clicked.connect(self.audience.pause)
        self.btn_stop.clicked.connect(self.audience.stop)
        self.btn_shuffle.clicked.connect(self.shuffle_and_show)
        self.vol_slider.valueChanged.connect(self.on_volume_changed)

        # ButtonGroups pro Spieler
        self.groups: Dict[str, QButtonGroup] = {}
        self.col_players: List[str] = []
        self.reveal_buttons: List[QPushButton] = []  # Referenzen, um Zustand/Style zu ändern

        # initiale Lautstärke setzen
        self.on_volume_changed(self.vol_slider.value())

        self.audience.set_global_preparing(True)

    # ----- Volume -----

    def on_volume_changed(self, value: int):
        # QAudioOutput erwartet 0.0–1.0; UI liefert 0–100
        vol = max(0.0, min(1.0, value / 100.0))
        try:
            self.audience.audio.setVolume(vol)
        except Exception:
            pass
        self.vol_label.setText(f"{value}%")

    # ----- Setup / Navigation -----

    def run_setup(self):
        dlg = SetupDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        self.players = dlg.players
        self.templates = dlg.template
        self.scores = {p: 0 for p in self.players}
        self.round_index = 0
        self.audience.configure_players(self.players)
        self.audience.set_global_preparing(False)
        # nach Setup sicherstellen, dass Lautstärke gesetzt ist
        self.on_volume_changed(self.vol_slider.value())
        self.refresh_round()

    def prev_round(self):
        if not self.templates:
            QMessageBox.information(self, "Hinweis", "Bitte zuerst Setup ausführen.")
            return
        if self.round_index > 0:
            self.round_index -= 1
            self.refresh_round()

    def next_round(self):
        if not self.templates:
            QMessageBox.information(self, "Hinweis", "Bitte zuerst Setup ausführen.")
            return
        if self.round_index < len(self.templates) - 1:
            self.round_index += 1
            self.refresh_round()

    def closeEvent(self, e):
        if self.on_close:
            self.on_close()
        super().closeEvent(e)

    # ----- Rundensicht -----

    def refresh_round(self):
        templ = self.templates[self.round_index] if self.templates else None
        if not templ:
            self.lbl_round.setText("Runde: –")
            self._rebuild_answer_inputs()
            self._rebuild_checkboxes([])
            self.audience.set_global_preparing(True)
            return

        self.lbl_round.setText(f"Runde: {templ.title}")
        self.audience.set_video(templ.video)
        self._rebuild_answer_inputs()

        self.runtime = RoundRuntime(
            players_answers=["" for _ in self.players],
            shuffled_order=[],
            revealed=[],
            selections={p: None for p in self.players},
        )

        self._rebuild_checkboxes([])
        self.audience.show_waiting_center()
        self.audience.set_scores(self.scores)

    def _rebuild_answer_inputs(self):
        layout: QGridLayout = self.answers_group.layout()
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.answer_edits.clear()

        templ_title = self.templates[self.round_index].title if self.templates else ""
        layout.addWidget(QLabel(f"Aktuelle Runde: {templ_title}"), 0, 0, 1, 2)
        for i, p in enumerate(self.players, start=1):
            layout.addWidget(QLabel(f"{p}:"), i, 0)
            e = QLineEdit()
            e.setPlaceholderText(f"Antwort von {p}")
            self.answer_edits[p] = e
            layout.addWidget(e, i, 1)
        layout.addWidget(QLabel("Richtige Antwort (aus Template):"), len(self.players)+1, 0)
        truth_lab = QLabel(self.templates[self.round_index].truth if self.templates else "")
        truth_lab.setStyleSheet("font-style: italic;")
        layout.addWidget(truth_lab, len(self.players)+1, 1)

    # ----- Mischen & Anzeigen / Auswahl / Aufdecken -----

    def shuffle_and_show(self):
        if not self.templates:
            QMessageBox.information(self, "Hinweis", "Bitte zuerst Setup ausführen.")
            return
        templ = self.templates[self.round_index]
        players_ans = [self.answer_edits[p].text() for p in self.players]
        slots = [{"author": p, "text": players_ans[i]} for i, p in enumerate(self.players)]
        slots.append({"author": "Richtige Antwort", "text": templ.truth})

        order = list(range(len(slots)))
        random.shuffle(order)
        revealed = [False] * len(order)
        self.runtime.players_answers = players_ans
        self.runtime.shuffled_order = order
        self.runtime.revealed = revealed
        self.runtime.selections = {p: None for p in self.players}

        self.col_players = self._rotated_players()
        self._rebuild_checkboxes(order)

        view_slots = [slots[i] for i in order]
        self.audience.set_answers_grid(view_slots, revealed, self.col_players, self.runtime.selections)
        self.audience.set_scores(self.scores)

    def _rotated_players(self) -> List[str]:
        dq = deque(self.players)
        dq.rotate(self.round_index % (len(self.players) if self.players else 1))
        return list(dq)

    def _rebuild_checkboxes(self, order: List[int]):
        for i in reversed(range(self.chk_grid.count())):
            item = self.chk_grid.takeAt(i)
            if item.widget():
                item.widget().deleteLater()
        self.groups = {}
        self.reveal_buttons = []

        if not order:
            self.chk_grid.addWidget(QLabel("Bitte 'Mischen & anzeigen' nutzen."), 0, 0)
            return

        templ = self.templates[self.round_index]
        base_slots = [{"author": p, "text": self.runtime.players_answers[i]} for i, p in enumerate(self.players)]
        base_slots.append({"author": "Richtige Antwort", "text": templ.truth})
        slots = [base_slots[i] for i in order]

        # Kopfzeile
        self.chk_grid.addWidget(QLabel("Autor / Antwort"), 0, 0)
        self.col_players = self._rotated_players()
        for c, pname in enumerate(self.col_players, start=1):
            lab = QLabel(pname); lab.setAlignment(Qt.AlignCenter)
            self.chk_grid.addWidget(lab, 0, c)
        self.chk_grid.addWidget(QLabel("Status"), 0, len(self.col_players)+1)

        # ButtonGroups
        for pname in self.col_players:
            grp = QButtonGroup(self)
            grp.setExclusive(True)
            grp.idClicked.connect(self._on_group_clicked_factory(pname))
            self.groups[pname] = grp

        # Zeilen
        for r, slot in enumerate(slots, start=1):
            # Autor + Antwort (Moderator sieht Autor)
            text = slot['text'] or "(leer)"
            author = slot['author']
            info = QLabel(f"{author} — {text[:100]}")
            self.chk_grid.addWidget(info, r, 0)

            # Auswahlspalten
            for c, pname in enumerate(self.col_players, start=1):
                cb = QCheckBox()
                cb.setStyleSheet(f"QCheckBox {{ margin: {CHK_PADDING}px; }}")
                self.groups[pname].addButton(cb, r-1)
                self.chk_grid.addWidget(cb, r, c, alignment=Qt.AlignCenter)
                if self.runtime.selections.get(pname) == (r-1):
                    cb.setChecked(True)

            # Aufdecken-Button
            btn = QPushButton("Aufdecken")
            style_button(btn, BTN_COLOR_SHOW)
            btn.clicked.connect(lambda _, i=r-1: self.reveal_row(i))
            self.reveal_buttons.append(btn)
            self.chk_grid.addWidget(btn, r, len(self.col_players)+1)

        # Bereits aufgedeckte Zeilen einfärben
        for idx, done in enumerate(self.runtime.revealed):
            if done and idx < len(self.reveal_buttons):
                self._mark_revealed_button(self.reveal_buttons[idx])

    def _mark_revealed_button(self, btn: QPushButton):
        btn.setText("Aufgedeckt")
        style_button(btn, BTN_COLOR_DONE)
        btn.setEnabled(False)

    def _on_group_clicked_factory(self, pname: str):
        def handler(row_index: int):
            self.runtime.selections[pname] = row_index
            self.audience.update_selection(pname, row_index)
        return handler

    def reveal_row(self, row_index: int):
        if not self.runtime.shuffled_order:
            QMessageBox.information(self, "Hinweis", "Bitte zuerst 'Mischen & anzeigen' nutzen.")
            return
        if not (0 <= row_index < len(self.runtime.revealed)):
            return
        if self.runtime.revealed[row_index]:
            return

        templ = self.templates[self.round_index]
        base_slots = [{"author": p, "text": self.runtime.players_answers[i]} for i, p in enumerate(self.players)]
        base_slots.append({"author": "Richtige Antwort", "text": templ.truth})
        abs_idx = self.runtime.shuffled_order[row_index]
        slot = base_slots[abs_idx]

        # Punkte
        if slot["author"] == "Richtige Antwort":
            for p, grp in self.groups.items():
                if grp.checkedId() == row_index:
                    self.scores[p] = self.scores.get(p, 0) + 1
        else:
            author = slot["author"]
            for p, grp in self.groups.items():
                if grp.checkedId() == row_index and p != author:
                    self.scores[author] = self.scores.get(author, 0) + 1

        self.runtime.revealed[row_index] = True

        # Zuschauer-Grid neu aufbauen (Autor sichtbar machen) und Selektionen persistieren
        view_slots = [base_slots[i] for i in self.runtime.shuffled_order]
        self.audience.set_answers_grid(view_slots, self.runtime.revealed, self.col_players, self.runtime.selections)
        self.audience.set_scores(self.scores)

        # Button "Aufgedeckt" markieren
        if 0 <= row_index < len(self.reveal_buttons):
            self._mark_revealed_button(self.reveal_buttons[row_index])

# ------------------------
# Wrapper (rückwärtskompatible Signatur)
# ------------------------

class BlindPickQuiz(QWidget):
    def __init__(self, on_close=None, state=None, on_state_change=None):
        super().__init__()
        self.setWindowTitle("Blind Pick — Wrapper")
        self.on_close = on_close

        self.audience = AudienceWindow()
        self.ctrl = ControlWindow(on_close=self.close_both, audience=self.audience)

        self.audience.show()
        self.ctrl.show()

    def close_both(self):
        self.audience.close()
        self.close()
        if self.on_close:
            self.on_close()

# Optional: Direktstart
if __name__ == "__main__":
    import sys
    app = QApplication(sys.argv)
    wrapper = BlindPickQuiz(on_close=None)
    wrapper.show()
    sys.exit(app.exec())
