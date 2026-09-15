"""Ventana principal de ConsolaOBS v2."""

from __future__ import annotations

import logging
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QHBoxLayout,
    QSplitter,
    QVBoxLayout,
    QWidget,
    QMainWindow,
)

from ..config.config_toml import Configuracion
from ..obs.cliente import ClienteOBS
from ..obs.eventos import SenalesOBS
from ..audio.motor_obs import MotorOBS
from ..audio.motor_local import MotorLocal
from ..audio.motor import ModoReproduccion

from .conexion_widget import ConexionWidget
from .panel_soundboard import PanelSoundboard
from .panel_mixer import PanelMixer

log = logging.getLogger(__name__)

# Tema oscuro global
QSS_GLOBAL = """
    * {
        font-family: "Segoe UI", Arial, sans-serif;
    }
    QMainWindow, QWidget {
        background-color: #1e1e1e;
        color: #e0e0e0;
    }
    QSplitter::handle {
        background: #333;
        width: 3px;
    }
    QToolTip {
        background: #333;
        color: #eee;
        border: 1px solid #555;
        border-radius: 3px;
        padding: 4px;
    }
"""


class VentanaPrincipal(QMainWindow):
    def __init__(self, config: Configuracion):
        super().__init__()
        self._config = config
        self._cliente = ClienteOBS()
        self._señales = SenalesOBS()
        self._motor_obs = MotorOBS()
        self._motor_local = MotorLocal()
        self._modo = ModoReproduccion(config.reproduccion.get("modo_global", "obs"))
        self._reproduciendo: set[int] = set()

        self.setWindowTitle("ConsolaOBS v2 — Panel de audio para OBS Studio")
        self.setMinimumSize(900, 600)
        self._aplicar_estilos()

        # ── Layout principal ──────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Barra de conexion
        self._conexion = ConexionWidget()
        self._conexion.senial_conectar.connect(self._on_conectar)
        self._conexion.senial_desconectar.connect(self._on_desconectar)
        layout.addWidget(self._conexion)

        # Splitter: Mixer | Soundboard
        self._splitter = QSplitter(Qt.Orientation.Horizontal)

        self._mixer = PanelMixer(config)
        self._soundboard = PanelSoundboard(config)

        self._splitter.addWidget(self._mixer)
        self._splitter.addWidget(self._soundboard)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 2)

        layout.addWidget(self._splitter, 1)

        # Restaurar geometria
        self._restaurar_geometria()

        # Conectar senales del soundboard
        self._soundboard.senial_play_pad.connect(self._on_toggle_pad)
        self._soundboard.senial_stop_pad.connect(self._on_stop_pad)
        self._soundboard.senial_pad_cambio.connect(self._guardar_config)

    # ── Estilos ─────────────────────────────────────────────────────────

    def _aplicar_estilos(self):
        self.setStyleSheet(QSS_GLOBAL)

    # ── Geometria ───────────────────────────────────────────────────────

    def _restaurar_geometria(self):
        geo = str(self._config.interfaz.get("geometria", "")).strip()
        maxim = bool(self._config.interfaz.get("maximizada", False))
        divisor = float(self._config.interfaz.get("posicion_divisor", 0.4) or 0.4)

        if geo:
            try:
                # Formato "WxH+X+Y"
                ancho, alto = geo.split("+")[0].split("x")
                x, y = geo.split("+")[1], geo.split("+")[2]
                self.resize(int(ancho), int(alto))
                self.move(int(x), int(y))
            except Exception:
                self.resize(1200, 800)
                self.move(100, 50)
        else:
            self.resize(1200, 800)
            self.move(100, 50)

        # Aplicar divisor guardado en el siguiente ciclo (los widgets ya existen)
        QTimer.singleShot(0, self._aplicar_divisor_guardado)
        if maxim:
            self.showMaximized()

    def _aplicar_divisor_guardado(self):
        try:
            total = max(self._splitter.width(), 1)
            divisor = float(self._config.interfaz.get("posicion_divisor", 0.4) or 0.4)
            izquierda = int(total * divisor)
            self._splitter.setSizes([izquierda, max(total - izquierda, 1)])
        except Exception:
            pass

    def closeEvent(self, event):
        # Guardar geometria
        geo = self.geometry()
        self._config.interfaz["geometria"] = f"{geo.width()}x{geo.height()}+{geo.x()}+{geo.y()}"
        self._config.interfaz["maximizada"] = self.isMaximized()
        self._config.interfaz["posicion_divisor"] = self._splitter.sizes()[0] / max(self._splitter.width(), 1)
        self._guardar_config()
        self._motor_obs.limpiar()
        self._motor_local.limpiar()
        event.accept()

    def _guardar_config(self):
        try:
            self._config.guardar()
        except Exception as e:
            log.warning("No se pudo guardar config: %s", e)

    # ── Conexion a OBS ─────────────────────────────────────────────────

    def _on_conectar(self, host: str, puerto: int, pass_: str):
        try:
            self._cliente.conectar(host, puerto, pass_)
            # Enlazar eventos
            self._señales.enlazar(self._cliente)
            # Inicializar motores
            self._motor_obs.inicializar(cliente=self._cliente)
            self._motor_local.inicializar(
                nombre_dispositivo=self._config.reproduccion.get("dispositivo_local", "VB-Audio Matrix")
            )
            # Cargar mixer
            self._mixer.enlazar(self._cliente, self._señales)
            self._mixer.cargar_fuentes()
            # Actualizar estado
            self._conexion.estado_conectado(True)
            # Guardar host/puerto
            self._config.conexion["host"] = host
            self._config.conexion["puerto"] = puerto
            # Guardar contrasena si se ingresa
            from ..config import credenciales
            if pass_:
                credenciales.guardar(pass_)
            self._guardar_config()
            log.info("Conectado a OBS %s:%s", host, puerto)
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Error de conexion", f"No se pudo conectar a OBS:\n{e}")
            self._conexion.estado_conectado(False)

    def _on_desconectar(self):
        self._señales.desenlazar(self._cliente)
        self._cliente.desconectar()
        self._motor_obs.limpiar()
        self._motor_local.limpiar()
        for idx in list(self._reproduciendo):
            self._reproduciendo.discard(idx)
            self._soundboard.marcar_sonando(idx, False)

    # ── Soundboard play/stop ───────────────────────────────────────────

    def _resolver_modo(self, indice: int) -> ModoReproduccion:
        pad = self._config.obtener_pad(indice)
        if pad and pad.get("modo") != "global":
            return ModoReproduccion(pad["modo"])
        return self._modo

    def _on_toggle_pad(self, indice: int):
        if indice in self._reproduciendo:
            self._on_stop_pad(indice)
        else:
            self._on_play_pad(indice)

    def _on_play_pad(self, indice: int):
        pad = self._config.obtener_pad(indice)
        if not pad or not pad.get("archivo"):
            return
        motor = self._motor_obs if self._resolver_modo(indice) == ModoReproduccion.OBS else self._motor_local
        motor.reproducir(pad["archivo"], pad_id=str(indice))
        self._reproduciendo.add(indice)
        self._soundboard.marcar_sonando(indice, True)

    def _on_stop_pad(self, indice: int):
        pad = self._config.obtener_pad(indice)
        if not pad:
            return
        motor = self._motor_obs if self._resolver_modo(indice) == ModoReproduccion.OBS else self._motor_local
        motor.detener(pad_id=str(indice))
        self._reproduciendo.discard(indice)
        self._soundboard.marcar_sonando(indice, False)

    def _intercambiar_pads(self, origen: int, destino: int):
        self._config.intercambiar_pads(origen, destino)
        self._soundboard._reconstruir()
        self._guardar_config()