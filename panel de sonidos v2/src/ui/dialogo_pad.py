"""Dialogo para crear un pad nuevo en el soundboard (sonido + nombre)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

EXT_SONIDO = "*.mp3 *.wav *.ogg *.flac"


class DialogoNuevoPad(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Nuevo pad de sonido")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        lbl_nombre = QLabel("Nombre:")
        lbl_nombre.setStyleSheet("color:#aaa;font-weight:bold;font-size:11px;")
        layout.addWidget(lbl_nombre)

        self._edit_nombre = QLineEdit()
        self._edit_nombre.setPlaceholderText("Ej: aplausos, risa, uy...")
        self._edit_nombre.setStyleSheet(
            "QLineEdit{background:#1e1e1e;color:#eee;border:1px solid #555;border-radius:4px;padding:6px;}"
        )
        layout.addWidget(self._edit_nombre)

        lbl_sonido = QLabel("Archivo de sonido:")
        lbl_sonido.setStyleSheet("color:#aaa;font-weight:bold;font-size:11px;")
        layout.addWidget(lbl_sonido)

        fila = QHBoxLayout()
        self._lbl_archivo = QLabel("Sin archivo seleccionado")
        self._lbl_archivo.setStyleSheet("color:#777;font-size:11px;")
        self._lbl_archivo.setMinimumWidth(220)
        fila.addWidget(self._lbl_archivo, 1)
        self._btn_buscar = QPushButton("Seleccionar sonido...")
        self._btn_buscar.clicked.connect(self._elegir_archivo)
        self._btn_buscar.setStyleSheet(
            "QPushButton{background:#4a9eff;color:#fff;border:none;border-radius:4px;padding:6px 12px;}"
            "QPushButton:hover{background:#3c8ade;}"
        )
        fila.addWidget(self._btn_buscar)
        layout.addLayout(fila)

        self._archivo = ""

        btns = QHBoxLayout()
        btns.addStretch()
        self._btn_ok = QPushButton("Crear pad")
        self._btn_ok.setEnabled(False)
        self._btn_ok.clicked.connect(self.accept)
        self._btn_ok.setStyleSheet(
            "QPushButton{background:#43a047;color:#fff;border:none;border-radius:4px;padding:6px 16px;font-weight:bold;}"
            "QPushButton:hover{background:#388e3c;}"
        )
        btns.addWidget(self._btn_ok)
        self._btn_cancel = QPushButton("Cancelar")
        self._btn_cancel.clicked.connect(self.reject)
        self._btn_cancel.setStyleSheet(
            "QPushButton{background:#333;color:#ccc;border:none;border-radius:4px;padding:6px 12px;}"
            "QPushButton:hover{background:#444;}"
        )
        btns.addWidget(self._btn_cancel)
        layout.addLayout(btns)

    def _elegir_archivo(self):
        archivo, _ = QFileDialog.getOpenFileName(self, "Seleccionar sonido", "", f"Sonidos ({EXT_SONIDO})")
        if archivo:
            self._archivo = archivo
            self._lbl_archivo.setText(archivo)
            self._lbl_archivo.setStyleSheet("color:#ddd;font-size:11px;")
            self._btn_ok.setEnabled(True)
            if not self._edit_nombre.text().strip():
                self._edit_nombre.setText(Path(archivo).stem)

    def resultado(self) -> tuple[str, str]:
        return self._edit_nombre.text().strip(), self._archivo