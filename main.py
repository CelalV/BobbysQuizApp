# main.py
# Voraussetzungen: pip install PySide6
# Start: python main.py

from __future__ import annotations
import sys
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QListWidget, QPushButton, QLabel, QMessageBox
from PySide6.QtCore import Qt

# Module (Editor optional, falls vorhanden)
from quiz_blindpick import BlindPickQuiz
try:
    from quiz_blindpick_editor import BlindPickEditor
except Exception:
    BlindPickEditor = None

QUIZ_REGISTRY = {
    "Blind Pick (Video + Antwortenmix)": BlindPickQuiz
}
if BlindPickEditor:
    QUIZ_REGISTRY["Blind Pick — Editor (Runden-Tool)"] = BlindPickEditor

class StartScreen(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Quiz Suite — Start")
        self.resize(650, 420)
        v = QVBoxLayout(self)

        v.addWidget(QLabel("Quiz-/Tool-Modul auswählen:"))
        self.list_mods = QListWidget()
        for name in QUIZ_REGISTRY.keys():
            self.list_mods.addItem(name)
        v.addWidget(self.list_mods, 1)

        self.btn_start = QPushButton("Starten")
        self.btn_start.clicked.connect(self.start_selected)
        v.addWidget(self.btn_start, alignment=Qt.AlignRight)

        self.module_win = None

    def start_selected(self):
        row = self.list_mods.currentRow()
        if row < 0:
            QMessageBox.critical(self, "Info", "Bitte ein Modul wählen.")
            return
        mod_name = self.list_mods.currentItem().text()
        cls = QUIZ_REGISTRY[mod_name]
        self.hide()
        self.module_win = cls(on_close=self.on_module_closed)
        self.module_win.show()

    def on_module_closed(self):
        self.show()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = StartScreen()
    w.show()
    sys.exit(app.exec())
