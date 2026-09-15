"""Configuracion e inicializacion de la QApplication."""

from __future__ import annotations

import sys
import logging
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from .config.config_toml import Configuracion
from .ui.ventana import VentanaPrincipal
from .config import credenciales

log = logging.getLogger(__name__)

APP_NOMBRE = "ConsolaOBS v2"
APP_VERSION = "2.0.0"


def configurar_logging():
    nivel = logging.DEBUG if "--debug" in sys.argv else logging.INFO
    logging.basicConfig(
        level=nivel,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def iniciar_app():
    configurar_logging()
    log.info("Iniciando %s v%s", APP_NOMBRE, APP_VERSION)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NOMBRE)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("ConsolaOBS")

    # Alta resolucion
    app.setStyle("Fusion")

    # Cargar configuracion
    config = Configuracion.cargar()
    log.info("Configuracion cargada correctamente")

    # Materializar config.toml en el primer arranque (plantilla editable a mano)
    from .config.config_toml import RUTA_CONFIG

    if not RUTA_CONFIG.exists():
        try:
            config.guardar()
            log.info("config.toml por defecto creado en: %s", RUTA_CONFIG)
        except Exception:
            log.exception("No se pudo crear config.toml")

    # Cargar contrasena recordada del Vault
    pass_guardada = credenciales.obtener()

    # Crear ventana principal
    ventana = VentanaPrincipal(config)
    # Cargar campos de conexion con valores recordados
    host = config.conexion.get("host", "localhost")
    puerto = config.conexion.get("puerto", 4455)
    ventana._conexion.cargar_campos(host, puerto, pass_guardada or "")

    ventana.show()

    log.info("Ventana mostrada")
    exito = app.exec()

    # Al salir, guardar config
    try:
        config.guardar()
    except Exception:
        pass

    sys.exit(exito)