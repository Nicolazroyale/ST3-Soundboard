"""Filtros de audio de una fuente de OBS: widget incrustable + dialogo."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

TIPOS_FILTROS = {
    "Gain": {"gain_db": -30.0},
    "Compressor": {"ratio_in_db": 3.0, "threshold": -18.0, "attack_time": 50, "release_time": 200},
    "Expander": {"ratio": 2.0, "threshold": -30.0, "attack_time": 5, "release_time": 100},
    "Noise Suppression": {"method": "RNNoise"},
    "Noise Gate": {"open_threshold": -26.0, "close_threshold": -32.0, "attack_time": 5, "hold_time": 200, "release_time": 100},
    "Limiter": {"threshold": -1.0, "release_time": 60},
    "Invert Polarity": {},
    "Async Delay": {"delay_ms": 0},
}


class WidgetFiltros(QWidget):
    """Lista de filtros de la fuente actual con agregar/eliminar/habilitar y settings JSON."""

    filtros_cambiados = Signal()

    def __init__(self, cliente, parent=None):
        super().__init__(parent)
        self._cliente = cliente
        self._fuente: str = ""
        self._filtros: list[dict] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._lbl_titulo = QLabel("Filtros")
        self._lbl_titulo.setStyleSheet("color:#aaa;font-weight:bold;font-size:11px;letter-spacing:1px;")
        layout.addWidget(self._lbl_titulo)

        self._lista = QListWidget()
        self._lista.currentItemChanged.connect(self._seleccion_cambio)
        self._lista.setMinimumHeight(110)
        layout.addWidget(self._lista, 1)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("Agregar")
        btn_add.clicked.connect(self._agregar_filtro)
        self._btn_remove = QPushButton("Eliminar")
        self._btn_remove.clicked.connect(self._eliminar_filtro)
        self._btn_remove.setEnabled(False)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(self._btn_remove)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._edit_settings = QPlainTextEdit()
        self._edit_settings.setPlaceholderText('{ "ajuste": valor, ... }')
        self._edit_settings.setMaximumHeight(90)
        layout.addWidget(self._edit_settings)

        self._btn_apply = QPushButton("Aplicar cambios")
        self._btn_apply.clicked.connect(self._aplicar_cambios)
        self._btn_apply.setEnabled(False)
        layout.addWidget(self._btn_apply)

    def asignar_cliente(self, cliente):
        self._cliente = cliente

    def mostrar_fuente(self, nombre: str):
        self._fuente = nombre
        self._lista.blockSignals(True)
        self._lista.clear()
        self._filtros.clear()
        self._edit_settings.clear()
        self._btn_apply.setEnabled(False)
        self._btn_remove.setEnabled(False)
        self._lista.blockSignals(False)
        if not self._cliente or not nombre:
            return
        try:
            self._filtros = list(self._cliente.filtros_fuente(nombre))
        except Exception:
            self._filtros = []
        self._lista.blockSignals(True)
        for f in self._filtros:
            item = QListWidgetItem(f"{f.get('filterName', '?')}  ({f.get('filterKind', '?')})")
            item.setData(Qt.ItemDataRole.UserRole, {"nombre": f.get("filterName", ""), "tipo": f.get("filterKind", "")})
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if f.get("filterEnabled", True) else Qt.CheckState.Unchecked)
            self._lista.addItem(item)
        self._lista.blockSignals(False)
        self._lista.itemChanged.connect(self._toggle_filtro)

    def _seleccion_cambio(self, item: QListWidgetItem | None, _prev):
        if not item:
            self._btn_apply.setEnabled(False)
            self._btn_remove.setEnabled(False)
            self._edit_settings.clear()
            return
        self._btn_apply.setEnabled(True)
        self._btn_remove.setEnabled(True)
        try:
            ajustes = self._filtros[self._lista.currentRow()].get("filterSettings", {})
        except Exception:
            ajustes = {}
        self._edit_settings.setPlainText(json.dumps(ajustes, indent=2, ensure_ascii=False))

    def _toggle_filtro(self, item: QListWidgetItem):
        datos = item.data(Qt.ItemDataRole.UserRole)
        if not datos:
            return
        habilitado = item.checkState() == Qt.CheckState.Checked
        try:
            self._cliente.habilitar_filtro(self._fuente, datos["nombre"], habilitado)
        except Exception:
            pass

    def _agregar_filtro(self):
        tipos = list(TIPOS_FILTROS.keys())
        tipo, ok = QInputDialog.getItem(self, "Agregar filtro", "Tipo:", tipos, 0, False)
        if not ok or not tipo:
            return
        nombre, ok2 = QInputDialog.getText(self, "Nombre del filtro", "Nombre:", text=tipo)
        if not ok2 or not nombre.strip():
            return
        try:
            self._cliente.configurar_filtro(self._fuente, nombre.strip(), tipo, TIPOS_FILTROS.get(tipo, {}))
            self.mostrar_fuente(self._fuente)
            self.filtros_cambiados.emit()
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def _eliminar_filtro(self):
        row = self._lista.currentRow()
        if row < 0:
            return
        datos = self._lista.item(row).data(Qt.ItemDataRole.UserRole)
        resp = QMessageBox.question(self, "Confirmar", f"Eliminar filtro '{datos['nombre']}'?")
        if resp == QMessageBox.StandardButton.Yes:
            try:
                self._cliente.eliminar_filtro(self._fuente, datos["nombre"])
                self.mostrar_fuente(self._fuente)
                self.filtros_cambiados.emit()
            except Exception as e:
                QMessageBox.warning(self, "Error", str(e))

    def _aplicar_cambios(self):
        row = self._lista.currentRow()
        if row < 0:
            return
        item = self._lista.item(row)
        datos = item.data(Qt.ItemDataRole.UserRole)
        try:
            ajustes = json.loads(self._edit_settings.toPlainText() or "{}")
        except json.JSONDecodeError as e:
            QMessageBox.warning(self, "JSON invalido", f"No se pudo leer el JSON:\n{e}")
            return
        try:
            self._cliente.ajustes_filtro_set(self._fuente, datos["nombre"], ajustes)
            QMessageBox.information(self, "OK", "Cambios aplicados.")
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))


class DialogoFiltros(QDialog):
    def __init__(self, cliente, nombre_fuente: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Filtros de: {nombre_fuente}")
        self.setMinimumSize(560, 460)
        layout = QVBoxLayout(self)
        self._widget = WidgetFiltros(cliente)
        layout.addWidget(self._widget, 1)
        self._widget.mostrar_fuente(nombre_fuente)
        btn_cerrar = QPushButton("Cerrar")
        btn_cerrar.clicked.connect(self.accept)
        layout.addWidget(btn_cerrar, alignment=Qt.AlignmentFlag.AlignRight)