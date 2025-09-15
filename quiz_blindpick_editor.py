# quiz_editor.py
# Integrierter Runden-Editor als Modul-Klasse (kein eigenständiges Script)
# Template-JSON: {"quiz_type":"blindpick","rounds":[{"title": "...","video": "...","truth":"..."}]}

from __future__ import annotations

import json
import sys
import copy
from dataclasses import dataclass, asdict
from typing import List, Optional

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QPushButton, QLabel, QLineEdit,
    QFileDialog, QMessageBox, QGridLayout, QSplitter, QSizePolicy, QCheckBox
)

@dataclass
class Round:
    title: str
    video: str
    truth: str

def default_round(n: int) -> Round:
    return Round(title=f"Runde {n}", video="", truth="")

class BlindPickEditor(QMainWindow):
    # on_close callback kompatibel mit Startscreen
    def __init__(self, on_close=None):
        super().__init__()
        self.setWindowTitle("Blind Pick — Runden-Editor")
        self.resize(1100, 700)
        self.on_close = on_close

        # Zustand
        self.rounds: List[Round] = []
        self.current_index: Optional[int] = None
        self.current_path: Optional[str] = None
        self.dirty: bool = False
        self._selection_changing: bool = False
        self._fields_updating: bool = False

        # Menüs/Aktionen
        self._build_menu()

        # UI
        splitter = QSplitter()
        splitter.setChildrenCollapsible(False)
        left = QWidget(); left.setMinimumWidth(340)
        right = QWidget()

        # Linke Seite: Rundenliste + Buttons
        lv = QVBoxLayout(left)
        self.list = QListWidget()
        self.list.model().rowsMoved.connect(self._on_rows_moved)
        self.list.currentRowChanged.connect(self._on_select_round)

        lv.addWidget(QLabel("Runden"))
        lv.addWidget(self.list, 1)

        hb = QHBoxLayout()
        self.btn_add = QPushButton("＋")
        self.btn_dup = QPushButton("Duplizieren")
        self.btn_del = QPushButton("–")
        hb.addWidget(self.btn_add)
        hb.addWidget(self.btn_dup)
        hb.addWidget(self.btn_del)
        lv.addLayout(hb)

        hb2 = QHBoxLayout()
        self.btn_up = QPushButton("↑ Hoch")
        self.btn_down = QPushButton("↓ Runter")
        hb2.addWidget(self.btn_up)
        hb2.addWidget(self.btn_down)
        lv.addLayout(hb2)

        # Rechte Seite: Formular
        rf = QGridLayout(right)
        row = 0

        rf.addWidget(QLabel("Titel:"), row, 0)
        self.ed_title = QLineEdit()
        rf.addWidget(self.ed_title, row, 1); row += 1

        rf.addWidget(QLabel("Video:"), row, 0)
        vbox = QHBoxLayout()
        self.ed_video = QLineEdit()
        self.ed_video.setPlaceholderText("Pfad zur Videodatei")
        self.btn_browse = QPushButton("…")
        vbox.addWidget(self.ed_video, 1)
        vbox.addWidget(self.btn_browse)
        rf.addLayout(vbox, row, 1); row += 1

        rf.addWidget(QLabel("Richtige Antwort:"), row, 0)
        self.ed_truth = QLineEdit()
        self.ed_truth.setPlaceholderText("Offizielle richtige Antwort")
        rf.addWidget(self.ed_truth, row, 1); row += 1

        rf.setRowStretch(row, 1)

        # Events
        self.btn_add.clicked.connect(self._add_round)
        self.btn_dup.clicked.connect(self._duplicate_round)
        self.btn_del.clicked.connect(self._delete_round)
        self.btn_up.clicked.connect(self._move_up)
        self.btn_down.clicked.connect(self._move_down)
        self.btn_browse.clicked.connect(self._choose_video)

        self.ed_title.editingFinished.connect(self._commit_fields)
        self.ed_video.editingFinished.connect(self._commit_fields)
        self.ed_truth.editingFinished.connect(self._commit_fields)
        self.ed_title.textEdited.connect(self._mark_dirty_typing)
        self.ed_video.textEdited.connect(self._mark_dirty_typing)
        self.ed_truth.textEdited.connect(self._mark_dirty_typing)

        splitter.addWidget(left)
        splitter.addWidget(right)
        self.setCentralWidget(splitter)

        # Neu starten
        self._new_document()

    # ---------- Menü ----------

    def _build_menu(self):
        m_file = self.menuBar().addMenu("&Datei")
        act_new = QAction("Neu", self); act_new.triggered.connect(self._new_document)
        act_open = QAction("Öffnen …", self); act_open.triggered.connect(self._open_document)
        act_save = QAction("Speichern", self); act_save.triggered.connect(self._save_document)
        act_save_as = QAction("Speichern unter …", self); act_save_as.triggered.connect(self._save_document_as)
        act_quit = QAction("Schließen", self); act_quit.triggered.connect(self.close)
        m_file.addAction(act_new)
        m_file.addAction(act_open)
        m_file.addSeparator()
        m_file.addAction(act_save)
        m_file.addAction(act_save_as)
        m_file.addSeparator()
        m_file.addAction(act_quit)

    # ---------- Datei-Operationen ----------

    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        ret = QMessageBox.question(
            self, "Ungespeicherte Änderungen",
            "Änderungen verwerfen?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        return ret == QMessageBox.Yes

    def _new_document(self):
        if not self._confirm_discard():
            return
        self.current_path = None
        self.rounds = [default_round(1)]
        self.dirty = False
        self._rebuild_list()
        self.list.setCurrentRow(0)
        self.statusBar().showMessage("Neues Dokument", 3000)

    def _open_document(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Template öffnen", "", "JSON (*.json)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Konnte Datei nicht lesen:\n{e}")
            return

        if data.get("quiz_type") != "blindpick":
            QMessageBox.warning(self, "Hinweis", "quiz_type ist nicht 'blindpick'. Trotzdem laden?")
        rounds = data.get("rounds", [])
        if not isinstance(rounds, list):
            QMessageBox.critical(self, "Fehler", "Ungültiges Format: 'rounds' fehlt oder ist kein Array.")
            return

        self.current_path = path
        self.rounds = []
        for i, r in enumerate(rounds, start=1):
            title = r.get("title") or f"Runde {i}"
            video = r.get("video") or ""
            truth = r.get("truth") or ""
            self.rounds.append(Round(title=title, video=video, truth=truth))

        self.dirty = False
        self._rebuild_list()
        if self.rounds:
            self.list.setCurrentRow(0)
        self.statusBar().showMessage(f"Geladen: {path}", 3000)

    def _validate(self) -> Optional[str]:
        if not self.rounds:
            return "Mindestens eine Runde wird benötigt."
        for i, r in enumerate(self.rounds, start=1):
            if not r.video.strip():
                return f"Runde {i}: Video darf nicht leer sein."
            if not r.truth.strip():
                return f"Runde {i}: Richtige Antwort darf nicht leer sein."
            if not r.title.strip():
                r.title = f"Runde {i}"
        return None

    def _save_document(self):
        if not self.current_path:
            return self._save_document_as()
        err = self._validate()
        if err:
            QMessageBox.information(self, "Validierung", err)
            return
        try:
            payload = {
                "quiz_type": "blindpick",
                "rounds": [asdict(r) for r in self.rounds]
            }
            with open(self.current_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Konnte nicht speichern:\n{e}")
            return
        self.dirty = False
        self.statusBar().showMessage(f"Gespeichert: {self.current_path}", 3000)

    def _save_document_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Template speichern", "", "JSON (*.json)"
        )
        if not path:
            return
        self.current_path = path
        self._save_document()

    # ---------- Liste / Selektion ----------

    def _rebuild_list(self):
        self._selection_changing = True
        try:
            self.list.clear()
            for r in self.rounds:
                item = QListWidgetItem(r.title or "(ohne Titel)")
                self.list.addItem(item)
        finally:
            self._selection_changing = False

    def _on_select_round(self, row: int):
        if self._selection_changing:
            return
        self.current_index = None if row < 0 else row
        self._load_fields_from_model()

    def _on_rows_moved(self, *args):
        # Sync interner Daten nach Drag&Drop
        try:
            start = args[6]
            end = args[7]
            dest = args[8]
        except Exception:
            order = [self.list.item(i).text() for i in range(self.list.count())]
            self._reorder_by_titles(order)
            self.dirty = True
            return

        if start == end:
            moved = self.rounds.pop(start)
            if dest > start:
                dest_index = dest - 1
            else:
                dest_index = dest
            self.rounds.insert(dest_index, moved)
            self.dirty = True
            self.list.setCurrentRow(dest_index)

    def _reorder_by_titles(self, order_titles: List[str]):
        title_to_round = {r.title: r for r in self.rounds}
        new_list = []
        for t in order_titles:
            if t in title_to_round:
                new_list.append(title_to_round.pop(t))
        new_list.extend(title_to_round.values())
        self.rounds = new_list

    # ---------- Formularbindung ----------

    def _load_fields_from_model(self):
        self._fields_updating = True
        try:
            if self.current_index is None or not (0 <= self.current_index < len(self.rounds)):
                self.ed_title.setText("")
                self.ed_video.setText("")
                self.ed_truth.setText("")
                self._set_form_enabled(False)
                return
            self._set_form_enabled(True)
            r = self.rounds[self.current_index]
            self.ed_title.setText(r.title)
            self.ed_video.setText(r.video)
            self.ed_truth.setText(r.truth)
        finally:
            self._fields_updating = False

    def _set_form_enabled(self, on: bool):
        self.ed_title.setEnabled(on)
        self.ed_video.setEnabled(on)
        self.ed_truth.setEnabled(on)
        self.btn_browse.setEnabled(on)

    def _commit_fields(self):
        if self._fields_updating:
            return
        if self.current_index is None or not (0 <= self.current_index < len(self.rounds)):
            return
        r = self.rounds[self.current_index]
        r.title = self.ed_title.text().strip() or r.title
        r.video = self.ed_video.text().strip()
        r.truth = self.ed_truth.text().strip()
        it = self.list.item(self.current_index)
        if it:
            it.setText(r.title or "(ohne Titel)")
        self.dirty = True

    def _mark_dirty_typing(self, *_):
        if not self._fields_updating:
            self.dirty = True

    # ---------- Button-Aktionen ----------

    def _add_round(self):
        n = len(self.rounds) + 1
        r = default_round(n)
        self.rounds.append(r)
        self.list.addItem(QListWidgetItem(r.title))
        self.list.setCurrentRow(self.list.count() - 1)
        self.dirty = True

    def _duplicate_round(self):
        idx = self.current_index
        if idx is None or not (0 <= idx < len(self.rounds)):
            return
        r = self.rounds[idx]
        nr = copy.deepcopy(r)
        nr.title = self._unique_copy_title(nr.title)
        self.rounds.insert(idx + 1, nr)
        self.list.insertItem(idx + 1, QListWidgetItem(nr.title))
        self.list.setCurrentRow(idx + 1)
        self.dirty = True

    def _unique_copy_title(self, title: str) -> str:
        base = f"{title} (Kopie)"
        candidate = base
        i = 2
        existing = {r.title for r in self.rounds}
        while candidate in existing:
            candidate = f"{base} {i}"
            i += 1
        return candidate

    def _delete_round(self):
        idx = self.current_index
        if idx is None or not (0 <= idx < len(self.rounds)):
            return
        if len(self.rounds) == 1:
            QMessageBox.information(self, "Hinweis", "Mindestens eine Runde wird benötigt.")
            return
        self.rounds.pop(idx)
        self.list.takeItem(idx)
        self.current_index = min(idx, len(self.rounds) - 1)
        self.list.setCurrentRow(self.current_index)
        self.dirty = True

    def _move_up(self):
        idx = self.current_index
        if idx is None or idx <= 0:
            return
        self.rounds[idx - 1], self.rounds[idx] = self.rounds[idx], self.rounds[idx - 1]
        it = self.list.takeItem(idx)
        self.list.insertItem(idx - 1, it)
        self.list.setCurrentRow(idx - 1)
        self.dirty = True

    def _move_down(self):
        idx = self.current_index
        if idx is None or idx >= len(self.rounds) - 1:
            return
        self.rounds[idx + 1], self.rounds[idx] = self.rounds[idx], self.rounds[idx + 1]
        it = self.list.takeItem(idx)
        self.list.insertItem(idx + 1, it)
        self.list.setCurrentRow(idx + 1)
        self.dirty = True

    def _choose_video(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Video auswählen",
            "", "Video (*.mp4 *.mkv *.mov *.avi *.webm *.m4v)"
        )
        if not path:
            return
        self.ed_video.setText(path)
        self._commit_fields()

    # ---------- Schließen ----------

    def closeEvent(self, event: QCloseEvent):
        # Speichern abfragen, dann schließen
        if not self._confirm_discard():
            event.ignore()
            return
        if self.on_close:
            self.on_close()
        super().closeEvent(event)
