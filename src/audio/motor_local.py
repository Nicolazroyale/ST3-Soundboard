"""Motor de reproduccion local via VB-Audio Virtual Matrix.

Usa ``miniaudio`` para decodificar y reproducir mp3/wav/ogg/flac directamente
a un dispositivo virtual ``VB-Matrix`` (detectado por nombre). Permite
reproduccion polifonica (varios pads simultaneos).
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import miniaudio

log = logging.getLogger(__name__)

# Busqueda de dispositivo virtual por defecto
DEVICE_MATCH = "VB-Audio Matrix"


def _buscar_dispositivo(nombre_contiene: str) -> dict | None:
    devs = miniaudio.Devices()
    for d in devs.get_playbacks():
        if nombre_contiene.lower() in d["name"].lower():
            return d
    return None


class _ReproductorActivo:
    """Wrapper de una reproduccion en curso."""

    def __init__(self, device_id, stream, pad_id: str):
        self.device_id = device_id
        self.stream = stream
        self.pad_id = pad_id
        self._device: miniaudio.PlaybackDevice | None = None

    def iniciar(self, vol: float = 1.0):
        self._device = miniaudio.PlaybackDevice(
            device_id=self.device_id,
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=2,
            sample_rate=44100,
            buffersize_msec=200,
        )
        self._device.start(self.stream)

    def detener(self):
        if self._device:
            try:
                self._device.close()
            except Exception:
                pass
            self._device = None


class MotorLocal:
    """Reproduce sonidos a traves de VB-Matrix."""

    nombre = "Local"

    def __init__(self):
        self._nombre_dispositivo: str = DEVICE_MATCH
        self._lock = threading.Lock()
        self._activos: dict[str, _ReproductorActivo] = {}  # pad_id -> activo
        self._device_id = None

    def inicializar(self, nombre_dispositivo: str | None = None, **_kw):
        if nombre_dispositivo:
            self._nombre_dispositivo = nombre_dispositivo
        self._actualizar_device_id()

    def _actualizar_device_id(self):
        dev = _buscar_dispositivo(self._nombre_dispositivo)
        if dev:
            self._device_id = dev["id"]
            log.info("Dispositivo local detectado: %s", dev["name"])
        else:
            self._device_id = None
            log.warning("No se detecto dispositivo '%s'", self._nombre_dispositivo)

    @property
    def dispositivo_disponible(self) -> bool:
        return self._device_id is not None

    def reproducir(self, archivo: str, pad_id: str = "0"):
        archivo = str(Path(archivo).resolve())
        with self._lock:
            # Detener si ya esta sonando el mismo pad
            if pad_id in self._activos:
                self._activos[pad_id].detener()
                del self._activos[pad_id]
            if not self._device_id:
                self._actualizar_device_id()
            if not self._device_id:
                log.warning("Dispositivo VB-Matrix no disponible.")
                return
            try:
                stream = miniaudio.stream_file(archivo)
                activo = _ReproductorActivo(self._device_id, stream, pad_id)
                activo.iniciar()
                self._activos[pad_id] = activo
                log.debug("Reproduciendo %s en pad %s", archivo, pad_id)
            except FileNotFoundError:
                log.warning("Archivo no encontrado: %s", archivo)
            except Exception:
                log.exception("Error reproduciendo %s", archivo)

    def detener(self, pad_id: str = "0"):
        with self._lock:
            if pad_id in self._activos:
                self._activos[pad_id].detener()
                del self._activos[pad_id]

    def reiniciar(self, archivo: str, pad_id: str = "0"):
        self.detener(pad_id)
        time.sleep(0.05)
        self.reproducir(archivo, pad_id)

    def detener_todo(self):
        with self._lock:
            for activo in self._activos.values():
                activo.detener()
            self._activos.clear()

    def limpiar(self):
        self.detener_todo()