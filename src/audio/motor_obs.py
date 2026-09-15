"""Motor de reproduccion vía OBS Studio.

Usa una fuente media ``Soundboard_Efectos`` (ffmpeg_source) compartida por todos
los pads. Solo permite un sonido a la vez (como el v1). El audio va a la mezcla
de OBS (stream/grabacion).
"""

from __future__ import annotations

import logging
from pathlib import Path

from obsws_python.error import OBSSDKError

from ..obs.cliente import (
    NOMBRE_FUENTE_EFECTOS,
    MEDIA_RESTART,
    MEDIA_STOP,
    ClienteOBS,
)

log = logging.getLogger(__name__)

AJUSTES_FUENTE = {
    "local_file": "",
    "is_local_file": True,
    "restart_on_activate": True,
    "close_when_inactive": True,
}


class MotorOBS:
    """Reproduce sonidos via una fuente media en OBS."""

    nombre = "OBS"

    def __init__(self):
        self._cliente: ClienteOBS | None = None

    def inicializar(self, cliente: ClienteOBS | None = None, **_kw):
        self._cliente = cliente
        if cliente and cliente.conectado:
            self._asegurar_fuente_existe()

    def limpiar(self):
        pass

    # -- fuente compartida ---------------------------------------------------

    def _asegurar_fuente_existe(self):
        """Verifica que ``Soundboard_Efectos`` exista; si no, la crea."""
        assert self._cliente
        try:
            fuentes = self._cliente.obtener_fuentes(tipo="ffmpeg_source")
            nombres = [f["inputName"] for f in fuentes]
            if NOMBRE_FUENTE_EFECTOS in nombres:
                return
        except OBSSDKError:
            return
        escena = self._cliente.escena_actual()
        try:
            self._cliente.crear_fuente(
                escena=escena,
                nombre=NOMBRE_FUENTE_EFECTOS,
                tipo="ffmpeg_source",
                ajustes=AJUSTES_FUENTE,
                habilitada=True,
            )
            log.info("Creada fuente %s en escena %s", NOMBRE_FUENTE_EFECTOS, escena)
        except OBSSDKError:
            log.exception("No se pudo crear %s", NOMBRE_FUENTE_EFECTOS)

    # -- interfaz MotorBase --------------------------------------------------

    def reproducir(self, archivo: str, pad_id: str = "0"):
        if not self._cliente or not self._cliente.conectado:
            log.warning("No conectado a OBS — no se puede reproducir.")
            return
        archivo_abs = str(Path(archivo).resolve())
        self._cliente.ajustes_fuente_set(
            NOMBRE_FUENTE_EFECTOS,
            {"local_file": archivo_abs},
        )
        self._cliente.disparar_accion_media(NOMBRE_FUENTE_EFECTOS, MEDIA_RESTART)

    def detener(self, pad_id: str = "0"):
        if not self._cliente or not self._cliente.conectado:
            return
        self._cliente.disparar_accion_media(NOMBRE_FUENTE_EFECTOS, MEDIA_STOP)

    def reiniciar(self, archivo: str, pad_id: str = "0"):
        self.detener(pad_id)
        self.reproducir(archivo, pad_id)

    def detener_todo(self):
        self.detener()