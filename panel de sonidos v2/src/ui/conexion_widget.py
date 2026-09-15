"""Widget de conexion a OBS.

Campos: host, puerto, contrasena + boton conectar/desconectar + chip de estado.
La contrasena se lee/guarda del Windows Credential Manager; host/puerto del config.toml.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger(__name__)


class ConexionWidget(QWidget):
    """Widget de conexion a OBS."""

    # Senales ---------------------------------------------------------------
    senial_conectar = Signal(str, int, str)  # host, puerto, password
    senial_desconectar = Signal()

    # Estilos ---------------------------------------------------------------
    STYLE = """
        ConexionWidget {
            background: #2b2b2b;
            border-radius: 6px;
            padding: 8px;
        }
        QLineEdit {
            background: #1e1e1e;
            color: #e0e0e0;
            border: 1px solid #555;
            border-radius: 4px;
            padding: 4px 8px;
            min-height: 22px;
        }
        QLineEdit:focus { border-color: #4a9eff; }
        QLabel { color: #aaa; font-size: 11px; }
        QPushButton {
            border: none;
            border-radius: 4px;
            padding: 6px 16px;
            font-weight: bold;
            font-size: 11px;
        }
        #btn_conectar {
            background: #4caf50;
            color: #fff;
        }
        #btn_conectar:hover { background: #5cbf60; }
        #btn_desc {
            background: #f44336;
            color: #fff;
        }
        #btn_desc:hover { background: #e55545; }
        #chip_conectado {
            background: #4caf50;
            color: #fff;
            border-radius: 8px;
            padding: 2px 10px;
            font-size: 10px;
            font-weight: bold;
        }
        #chip_desconectado {
            background: #666;
            color: #ccc;
            border-radius: 8px;
            padding: 2px 10px;
            font-size: 10px;
            font-weight: bold;
        }
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet(self.STYLE)
        self.conectado = False
        self._host = ""
        self._puerto = 4455

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        # Fila de campos
        fila = QHBoxLayout()
        fila.setSpacing(6)

        self._campo_host = QLineEdit("localhost")
        self._campo_host.setPlaceholderText("Host")
        self._campo_host.setFixedWidth(150)

        self._campo_puerto = QLineEdit("4455")
        self._campo_puerto.setPlaceholderText("Puerto")
        self._campo_puerto.setFixedWidth(70)

        self._campo_pass = QLineEdit()
        self._campo_pass.setPlaceholderText("Contrasena OBS")
        self._campo_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self._campo_pass.setFixedWidth(150)

        self._btn = QPushButton("CONECTAR")
        self._btn.setObjectName("btn_conectar")
        self._btn.clicked.connect(self._alternar_conexion)

        self._chip = QLabel("DESCONECTADO")
        self._chip.setObjectName("chip_desconectado")

        fila.addWidget(self._campo_host)
        fila.addWidget(self._campo_puerto)
        fila.addWidget(self._campo_pass)
        fila.addWidget(self._btn)
        fila.addStretch()
        fila.addWidget(self._chip)

        layout.addLayout(fila)

        # Guardar contraseña recordada
        self._recordar = True

    # -- api publica --------------------------------------------------------

    def cargar_campos(self, host: str, puerto: int, pass_guardada: str | None):
        self._campo_host.setText(host)
        self._campo_puerto.setText(str(puerto))
        if pass_guardada:
            self._campo_pass.setText(pass_guardada)

    def estado_conectado(self, conectado: bool):
        self.conectado = conectado
        if conectado:
            self._btn.setText("DESCONECTAR")
            self._btn.setObjectName("btn_desc")
            self._chip.setText("CONECTADO")
            self._chip.setObjectName("chip_conectado")
        else:
            self._btn.setText("CONECTAR")
            self._btn.setObjectName("btn_conectar")
            self._chip.setText("DESCONECTADO")
            self._chip.setObjectName("chip_desconectado")
        # Refrescar estilos (los objectName cambiaron)
        self._btn.setStyleSheet(self._btn.styleSheet())
        self._chip.setStyleSheet(self._chip.styleSheet())
        for w in (self._btn, self._chip):
            w.style().unpolish(w)
            w.style().polish(w)

    def _alternar_conexion(self):
        if self.conectado:
            self.senial_desconectar.emit()
            self.estado_conectado(False)
            return
        host = self._campo_host.text().strip() or "localhost"
        try:
            puerto = int(self._campo_puerto.text().strip() or "4455")
        except ValueError:
            puerto = 4455
        pass_ = self._campo_pass.text()
        self.senial_conectar.emit(host, puerto, pass_)