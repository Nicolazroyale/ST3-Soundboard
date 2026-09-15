"""Panel del soundboard: grid de pads + boton de agregar."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QFileDialog, QDialog, QGridLayout, QLabel, QInputDialog, QMenu, QScrollArea, QVBoxLayout, QWidget

from ..config.config_toml import EXT_TAMANO_PAD
from .dialogo_pad import DialogoNuevoPad
from .pad_widget import PadWidget

log = logging.getLogger(__name__)

EXT_SONIDO = "*.mp3 *.wav *.ogg *.flac"
EXT_IMAGEN = "*.png *.jpg *.jpeg *.gif *.bmp"

COLORES_SUGERIDOS = [
    "#43a047", "#e53935", "#1e88e5", "#fb8c00",
    "#8e24aa", "#00897b", "#fdd835", "#6d4c41",
]


class PanelSoundboard(QWidget):
    """Grid de pads con drag & drop, menu contextual y boton agregar."""

    senial_play_pad = Signal(int)
    senial_stop_pad = Signal(int)
    senial_pad_cambio = Signal()  # config de pads cambio (guardar)

    def __init__(self, configuracion, parent=None):
        super().__init__(parent)
        self._config = configuracion
        self._pads_w: list[PadWidget] = []

        self._titulo = QLabel("SOUNDBOARD")
        self._titulo.setStyleSheet("color:#aaa; font-size:12px; font-weight:bold; letter-spacing:1px;")

        self._area = QScrollArea()
        self._area.setWidgetResizable(True)
        self._area.setStyleSheet("QScrollArea{background:#1e1e1e;border:none;}")

        self._contenedor = QWidget()
        self._grid = QGridLayout()
        self._grid.setSpacing(10)
        self._contenedor.setLayout(self._grid)
        self._area.setWidget(self._contenedor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        layout.addWidget(self._titulo)
        layout.addWidget(self._area)

        self._reconstruir()

    # -- construccion ---------------------------------------------------------

    def _tamano_pad(self) -> int:
        return EXT_TAMANO_PAD.get(self._config.interfaz["tamano_iconos"], 128)

    def _reconstruir(self):
        # Limpiar grid
        for i in reversed(range(self._grid.count())):
            w = self._grid.itemAt(i).widget()
            if w:
                self._grid.removeWidget(w)
                w.deleteLater()

        self._pads_w.clear()
        tam = self._tamano_pad()
        columnas = int(self._config.soundboard.get("columnas", 4))
        total = len(self._config.pads)

        # Alineacion: insertar el boton "+" en la celda despues del ultimo pad
        for idx, pad in enumerate(self._config.pads):
            w = PadWidget(idx, pad, tam)
            w.senial_play.connect(self.senial_play_pad)
            w.senial_stop.connect(self.senial_stop_pad)
            w.senial_menu.connect(self._menu_pad)
            w.senial_swap.connect(self._intercambiar)
            self._grid.addWidget(w, idx // columnas, idx % columnas)
            self._pads_w.append(w)

        # Boton agregar
        self._btn_agregar = QLabel("+")
        self._btn_agregar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._btn_agregar.setFixedSize(tam, tam)
        self._btn_agregar.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_agregar.setStyleSheet(
            "QLabel{background:#2d2d2d;border:1px dashed #666;border-radius:12px;"
            "color:#999;font-size:28px;}"
            "QLabel:hover{background:#3a3a3a;color:#ddd;}"
        )
        self._grid.addWidget(self._btn_agregar, total // columnas, total % columnas)
        self._btn_agregar.mousePressEvent = self._agregar_pad_click

        # Relleno
        self._grid.setRowStretch(total // columnas + 1, 1)

    # -- interaccion ----------------------------------------------------------

    def _intercambiar(self, origen: int, destino: int):
        self._config.intercambiar_pads(origen, destino)
        self._reconstruir()
        self.senial_pad_cambio.emit()

    def _agregar_pad_click(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._agregar_pad()

    def _agregar_pad(self):
        dlg = DialogoNuevoPad(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        nombre, archivo = dlg.resultado()
        if not archivo:
            return
        pad = {
            "nombre": nombre or Path(archivo).stem,
            "archivo": archivo,
            "imagen": "",
            "color": COLORES_SUGERIDOS[len(self._config.pads) % len(COLORES_SUGERIDOS)],
            "modo": "global",
        }
        self._config.agregar_pad(pad)
        self._reconstruir()
        self.senial_pad_cambio.emit()

    def _menu_pad(self, indice: int, pos_global):
        menu = QMenu(self)
        act_play = menu.addAction("Reproducir")
        act_stop = menu.addAction("Detener")
        menu.addSeparator()
        act_sonido = menu.addAction("Asignar sonido...")
        act_imagen = menu.addAction("Asignar imagen...")
        act_nombre = menu.addAction("Renombrar...")
        act_color = menu.addAction("Color...")
        menu.addSeparator()

        # Submenu modo
        modo_actual = self._config.pads[indice].get("modo", "global")
        menu_modo = menu.addMenu("Modo de reproduccion")
        opciones = [("global", "Global (como el panel)"), ("local", "Local (VB-Matrix)"), ("obs", "Via OBS")]
        for valor, etiqueta in opciones:
            act = menu_modo.addAction(etiqueta)
            if valor == modo_actual:
                act.setEnabled(False)

        menu.addSeparator()
        act_eliminar = menu.addAction("Eliminar pad")

        seleccion = menu.exec(pos_global)
        if seleccion is None:
            return

        if seleccion == act_play:
            self.senial_play_pad.emit(indice)
        elif seleccion == act_stop:
            self.senial_stop_pad.emit(indice)
        elif seleccion == act_sonido:
            self._asignar_sonido(indice)
        elif seleccion == act_imagen:
            self._asignar_imagen(indice)
        elif seleccion == act_nombre:
            self._renombrar(indice)
        elif seleccion == act_color:
            self._elegir_color(indice)
        elif seleccion == act_eliminar:
            self._eliminar(indice)
        elif seleccion in menu_modo.actions():
            for valor, act in zip([o[0] for o in opciones], menu_modo.actions()):
                if seleccion == act:
                    self._config.actualizar_pad(indice, {"modo": valor})
                    break
            self._reconstruir()
            self.senial_pad_cambio.emit()

    # -- acciones -------------------------------------------------------------

    def _asignar_sonido(self, indice: int):
        archivo, _ = QFileDialog.getOpenFileName(
            self, "Seleccionar sonido", "", f"Sonidos ({EXT_SONIDO})"
        )
        if archivo:
            self._config.actualizar_pad(indice, {"archivo": archivo})
            self._reconstruir()
            self.senial_pad_cambio.emit()

    def _asignar_imagen(self, indice: int):
        archivo, _ = QFileDialog.getOpenFileName(
            self, "Seleccionar imagen", "", f"Imagenes ({EXT_IMAGEN})"
        )
        if archivo:
            self._config.actualizar_pad(indice, {"imagen": archivo})
            self._reconstruir()
            self.senial_pad_cambio.emit()

    def _renombrar(self, indice: int):
        actual = self._config.pads[indice].get("nombre", "")
        nombre, ok = QInputDialog.getText(self, "Renombrar pad", "Nombre:", text=actual)
        if ok and nombre.strip():
            self._config.actualizar_pad(indice, {"nombre": nombre.strip()})
            self._reconstruir()
            self.senial_pad_cambio.emit()

    def _elegir_color(self, indice: int):
        color, ok = QInputDialog.getText(
            self, "Color del pad", "Color (hex, ejemplo #43a047):",
            text=self._config.pads[indice].get("color", "#43a047"),
        )
        if ok and color.strip():
            self._config.actualizar_pad(indice, {"color": color.strip()})
            self._reconstruir()
            self.senial_pad_cambio.emit()

    def _eliminar(self, indice: int):
        self._config.eliminar_pad(indice)
        self._reconstruir()
        self.senial_pad_cambio.emit()

    # -- estado de reproduccion de pads ---------------------------------------

    def marcar_sonando(self, indice: int, sonando: bool):
        if 0 <= indice < len(self._pads_w):
            if sonando:
                self._pads_w[indice].iniciar_sonido()
            else:
                self._pads_w[indice].detener_sonido()