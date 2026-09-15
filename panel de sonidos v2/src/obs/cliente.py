"""Cliente OBS WebSocket v5, thread-safe.

Envuelve ``ReqClient`` (requests) y ``EventClient`` (eventos) dentro de
``_ClienteOBSSincronizado`` que serializa los requests con un ``threading.Lock``.
"""

from __future__ import annotations

import logging
import threading

from obsws_python import EventClient, ReqClient, Subs
from obsws_python.error import OBSSDKError, OBSSDKRequestError

log = logging.getLogger(__name__)

# Eventos de alto volumen que pedimos (VU meters, etc.)
SUBS = Subs.LOW_VOLUME | Subs.INPUTVOLUMEMETERS

# Nombre de la fuente media interna del soundboard
NOMBRE_FUENTE_EFECTOS = "Soundboard_Efectos"

# Acciones de media input
MEDIA_RESTART = "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART"
MEDIA_STOP = "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP"


class _ClienteOBSSincronizado:
    """Wrapper fino sobre ``ReqClient`` con todas las llamadas serializadas."""

    def __init__(self, cliente: ReqClient):
        self._c = cliente
        self._lock = threading.Lock()

    def __getattr__(self, nombre: str):
        attr = getattr(self._c, nombre)
        if not callable(attr):
            return attr
        with self._lock:
            return attr

    # Aliases de comodidad ---------------------------------------------------

    def pedido(self, nombre_metodo: str, **kwargs):
        """Ejecuta ``self.<nombre_metodo>(**kwargs)`` de forma thread-safe."""
        with self._lock:
            return getattr(self._c, nombre_metodo)(**kwargs)

    def version(self):
        return self.pedido("get_version")

    def desconectar(self):
        try:
            self._c.disconnect()
        except Exception:
            pass


class ClienteOBS:
    """Interfaz publica para comunicarte con OBS.

    Uso::

        cliente = ClienteOBS()
        cliente.conectar("localhost", 4455, "pass")
        info = cliente.obtener_fuentes()
        cliente.desconectar()
    """

    def __init__(self):
        self._req: _ClienteOBSSincronizado | None = None
        self._event: EventClient | None = None
        self.conectado = False

    # -- conexion -----------------------------------------------------------

    def conectar(self, host: str, puerto: int, contrasena: str) -> bool:
        if self.conectado:
            self.desconectar()
        try:
            cliente_req = ReqClient(
                host=host, port=puerto, password=contrasena
            )
            self._req = _ClienteOBSSincronizado(cliente_req)
            # Verificar que funciona
            self._req.version()
            self._event = EventClient(
                host=host, port=puerto, password=contrasena, subs=SUBS
            )
            self.conectado = True
            log.info("Conectado a OBS %s:%s", host, puerto)
            return True
        except OBSSDKError as exc:
            log.warning("Error de conexion OBS: %s", exc)
            self.desconectar()
            raise
        except Exception:
            log.exception("Error inesperado conectando a OBS")
            self.desconectar()
            raise

    def desconectar(self):
        if self._event:
            try:
                self._event.disconnect()
            except Exception:
                pass
            self._event = None
        if self._req:
            self._req.desconectar()
            self._req = None
        self.conectado = False
        log.info("Desconectado de OBS")

    # -- registro de callbacks (eventos) ------------------------------------

    def registrar_eventos(self, *callbacks):
        """Registra funciones callback para eventos de OBS.

        El nombre de cada funcion debe ser ``on_<evento_en_snake_case>``.
        """
        if not self._event:
            raise RuntimeError("No conectado a OBS")
        self._event.callback.register(list(callbacks))

    def deregistrar_eventos(self, *callbacks):
        if not self._event:
            return
        self._event.callback.deregister(list(callbacks))

    # -- peticiones de lectura -----------------------------------------------

    def _pedir(self, metodo: str, **kwargs):
        if not self._req:
            raise RuntimeError("No conectado a OBS")
        return self._req.pedido(metodo, **kwargs)

    def obtener_fuentes(self, tipo: str | None = None) -> list[dict]:
        resp = self._pedir("get_input_list", kind=tipo)
        return resp.inputs

    def obtener_fuentes_con_audio(self) -> list[dict]:
        """Fuentes que pueden emitir audio (por capabilities)."""
        return [f for f in self.obtener_fuentes() if f.get("inputKindCaps", 0) & 2]

    def entradas_especiales(self) -> dict:
        resp = self._pedir("get_special_inputs")
        claves = ("desktop1", "desktop2", "mic1", "mic2", "mic3", "mic4")
        return {k: getattr(resp, k, None) for k in claves}

    def escena_actual(self) -> str:
        resp = self._pedir("get_current_program_scene")
        return resp.current_program_scene_name

    def lista_escenas(self) -> list[dict]:
        resp = self._pedir("get_scene_list")
        return resp.scenes

    def lista_items_escena(self, escena: str) -> list[dict]:
        resp = self._pedir("get_scene_item_list", name=escena)
        return resp.scene_items

    def volumen_fuente(self, nombre: str) -> dict:
        resp = self._pedir("get_input_volume", name=nombre)
        return {
            "input_volume_mul": resp.input_volume_mul,
            "input_volume_db": resp.input_volume_db,
        }

    def mudo_fuente(self, nombre: str) -> bool:
        resp = self._pedir("get_input_mute", name=nombre)
        return resp.input_muted

    def filtros_fuente(self, nombre: str) -> list[dict]:
        resp = self._pedir("get_source_filter_list", name=nombre)
        return resp.filters

    def monitor_fuente(self, nombre: str) -> str:
        resp = self._pedir("get_input_audio_monitor_type", name=nombre)
        return resp.monitor_type

    def monitor_fuente_set(self, nombre: str, tipo: str):
        self._pedir("set_input_audio_monitor_type", name=nombre, mon_type=tipo)

    def tipos_filtros_disponibles(self) -> list[str]:
        resp = self._pedir("get_source_filter_kind_list")
        return resp.filter_kinds

    def ajustes_fuente(self, nombre: str) -> dict:
        resp = self._pedir("get_input_settings", name=nombre)
        return vars(resp)

    # -- peticiones de escritura ---------------------------------------------

    def volumen_fuente_set(self, nombre: str, vol_db: float | None = None, vol_mul: float | None = None):
        kwargs: dict = {"name": nombre}
        if vol_db is not None:
            kwargs["vol_db"] = vol_db
        if vol_mul is not None:
            kwargs["vol_mul"] = vol_mul
        self._pedir("set_input_volume", **kwargs)

    def mudo_fuente_set(self, nombre: str, mudo: bool):
        self._pedir("set_input_mute", name=nombre, muted=mudo)

    def cambiar_nombre_fuente(self, viejo: str, nuevo: str):
        self._pedir("set_input_name", old_name=viejo, new_name=nuevo)

    def ajustes_fuente_set(self, nombre: str, ajustes: dict, sobreponer: bool = True):
        self._pedir(
            "set_input_settings",
            name=nombre,
            settings=ajustes,
            overlay=sobreponer,
        )

    def crear_fuente(
        self,
        escena: str,
        nombre: str,
        tipo: str,
        ajustes: dict,
        habilitada: bool = True,
    ) -> dict:
        resp = self._pedir(
            "create_input",
            sceneName=escena,
            inputName=nombre,
            inputKind=tipo,
            inputSettings=ajustes,
            sceneItemEnabled=habilitada,
        )
        return vars(resp)

    def crear_item_escena(self, escena: str, fuente: str, habilitada: bool | None = None) -> dict:
        resp = self._pedir(
            "create_scene_item",
            scene_name=escena,
            source_name=fuente,
            enabled=habilitada,
        )
        return vars(resp)

    def habilitar_item_escena(self, escena: str, item_id: int, habilitada: bool):
        self._pedir(
            "set_scene_item_enabled",
            scene_name=escena,
            item_id=item_id,
            enabled=habilitada,
        )

    def disparar_accion_media(self, nombre: str, accion: str):
        self._pedir(
            "trigger_media_input_action",
            name=nombre,
            action=accion,
        )

    def configurar_filtro(
        self,
        fuente: str,
        nombre_filtro: str,
        tipo: str,
        ajustes: dict,
    ):
        self._pedir(
            "create_source_filter",
            source_name=fuente,
            filter_name=nombre_filtro,
            filter_kind=tipo,
            filter_settings=ajustes,
        )

    def habilitar_filtro(self, fuente: str, nombre_filtro: str, habilitado: bool):
        self._pedir(
            "set_source_filter_enabled",
            source_name=fuente,
            filter_name=nombre_filtro,
            enabled=habilitado,
        )

    def ajustes_filtro_set(
        self,
        fuente: str,
        nombre_filtro: str,
        ajustes: dict,
        sobreponer: bool = True,
    ):
        self._pedir(
            "set_source_filter_settings",
            source_name=fuente,
            filter_name=nombre_filtro,
            filter_settings=ajustes,
            overlay=sobreponer,
        )

    def eliminar_filtro(self, fuente: str, nombre_filtro: str):
        self._pedir(
            "remove_source_filter",
            source_name=fuente,
            filter_name=nombre_filtro,
        )