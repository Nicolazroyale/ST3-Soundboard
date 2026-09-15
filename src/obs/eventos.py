"""Señales Qt para eventos de OBS.

Los eventos llegan en el hilo de WebSocket; aqui los convertimos en señales
Qt para que el UI los consuma de forma segura (hilo principal).
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot


class _InyectorSenales(QObject):
    """Receptor interno: un slot recibe tuplas y re-emite la señal Qt."""

    _escena_programa_cambiada = Signal(str)
    _fuente_creada = Signal(str)
    _fuente_eliminada = Signal(str)
    _fuente_renombrada = Signal(str, str)   # viejo, nuevo
    _mudo_cambiado = Signal(str, bool)       # nombre, muted
    _volumen_cambiado = Signal(str, float, float)  # nombre, mul, db
    _items_escena_cambiados = Signal(str)
    _filtro_creado = Signal(str)
    _filtro_eliminado = Signal(str)
    _filtro_habilitado = Signal(str, str, bool)  # fuente, filtro, enabled
    _vu_meters = Signal(object)  # dict: {nombre: [peak_l, peak_r]}
    _conexion_perdida = Signal()

    @Slot(object)
    def _on_input_volume_meters(self, data):
        self._vu_meters.emit(data)

    @Slot(object)
    def _on_current_program_scene_changed(self, data):
        self._escena_programa_cambiada.emit(data.scene_name)

    @Slot(object)
    def _on_input_created(self, data):
        self._fuente_creada.emit(data.input_name)

    @Slot(object)
    def _on_input_removed(self, data):
        self._fuente_eliminada.emit(data.input_name)

    @Slot(object)
    def _on_input_name_changed(self, data):
        self._fuente_renombrada.emit(data.old_input_name, data.new_input_name)

    @Slot(object)
    def _on_input_mute_state_changed(self, data):
        self._mudo_cambiado.emit(data.input_name, data.input_muted)

    @Slot(object)
    def _on_input_volume_changed(self, data):
        self._volumen_cambiado.emit(
            data.input_name,
            data.input_volume_mul,
            data.input_volume_db,
        )

    @Slot(object)
    def _on_scene_item_created(self, data):
        self._items_escena_cambiados.emit(data.scene_name)

    @Slot(object)
    def _on_scene_item_removed(self, data):
        self._items_escena_cambiados.emit(data.scene_name)

    @Slot(object)
    def _on_scene_item_enable_state_changed(self, data):
        self._items_escena_cambiados.emit(data.scene_name)

    @Slot(object)
    def _on_source_filter_created(self, data):
        self._filtro_creado.emit(data.source_name)

    @Slot(object)
    def _on_source_filter_removed(self, data):
        self._filtro_eliminado.emit(data.source_name)

    @Slot(object)
    def _on_source_filter_enable_state_changed(self, data):
        self._filtro_habilitado.emit(data.source_name, data.filter_name, data.filter_enabled)


class SenalesOBS:
    """Caja de señales Qt para eventos de OBS."""

    def __init__(self):
        self._iny = _InyectorSenales()
        # Aliases publicos
        self.escena_programa_cambiada = self._iny._escena_programa_cambiada
        self.fuente_creada = self._iny._fuente_creada
        self.fuente_eliminada = self._iny._fuente_eliminada
        self.fuente_renombrada = self._iny._fuente_renombrada
        self.mudo_cambiado = self._iny._mudo_cambiado
        self.volumen_cambiado = self._iny._volumen_cambiado
        self.items_escena_cambiados = self._iny._items_escena_cambiados
        self.filtro_creado = self._iny._filtro_creado
        self.filtro_eliminado = self._iny._filtro_eliminado
        self.filtro_habilitado = self._iny._filtro_habilitado
        self.vu_meters = self._iny._vu_meters
        self.conexion_perdida = self._iny._conexion_perdida

    @property
    def _callbacks(self) -> list:
        return [
            self._iny._on_input_volume_meters,
            self._iny._on_current_program_scene_changed,
            self._iny._on_input_created,
            self._iny._on_input_removed,
            self._iny._on_input_name_changed,
            self._iny._on_input_mute_state_changed,
            self._iny._on_input_volume_changed,
            self._iny._on_scene_item_created,
            self._iny._on_scene_item_removed,
            self._iny._on_scene_item_enable_state_changed,
            self._iny._on_source_filter_created,
            self._iny._on_source_filter_removed,
            self._iny._on_source_filter_enable_state_changed,
        ]

    def enlazar(self, cliente):
        """Registra los callbacks en el ``ClienteOBS``."""
        cliente.registrar_eventos(*self._callbacks)

    def desenlazar(self, cliente):
        cliente.deregistrar_eventos(*self._callbacks)