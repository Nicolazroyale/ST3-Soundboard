"""Carga y guardado de la configuracion centralizada (config.toml).

Toda la configuracion de la app vive en UN solo archivo TOML ubicado junto al
ejecutable (o en la raiz del proyecto en desarrollo). Las credenciales NO van
aqui: la contrasena de OBS se guarda en el Windows Credential Manager (Vault).
"""

from __future__ import annotations

import copy
import sys
import tomllib
from pathlib import Path

import tomli_w

# Rutas de configuracion ---------------------------------------------------

if getattr(sys, "frozen", False):
    RUTA_BASE = Path(sys.executable).resolve().parent
else:
    RUTA_BASE = Path(__file__).resolve().parents[2]

RUTA_CONFIG = RUTA_BASE / "config.toml"

# Valores por defecto -------------------------------------------------------

CONFIG_DEFECTO: dict = {
    "conexion": {
        "host": "localhost",
        "puerto": 4455,
    },
    "reproduccion": {
        # "obs"   -> via fuente media de OBS (un sonido a la vez)
        # "local" -> via VB-Audio Virtual Matrix (polifonico)
        "modo_global": "obs",
        # Subcadena usada para detectar el dispositivo virtual VB-Matrix
        "dispositivo_local": "VB-Audio Matrix",
    },
    "interfaz": {
        # Cadena QRect "ancho x alto +x +y"; vacio = centrada por defecto
        "geometria": "",
        "maximizada": False,
        # "chico" | "mediano" | "grande"
        "tamano_iconos": "mediano",
        "posicion_divisor": 0.40,
        # Vista del mixer: "compacta" | "detalle"
        "vista_mixer": "detalle",
    },
    "soundboard": {
        "columnas": 4,
        "volumen_local": 0.9,
    },
}

EXT_TAMANO_PAD = {
    "chico": 96,
    "mediano": 128,
    "grande": 160,
}


def _fusionar(base: dict, extra: dict) -> dict:
    """Fusiona recursivamente ``extra`` sobre ``base`` respetando claves por defecto."""
    resultado = copy.deepcopy(base)
    for clave, valor in extra.items():
        if isinstance(valor, dict) and isinstance(resultado.get(clave), dict):
            resultado[clave] = _fusionar(resultado[clave], valor)
        else:
            resultado[clave] = copy.deepcopy(valor)
    return resultado


def _filtro_claves(base: dict, datos: dict) -> dict:
    """Se queda solo con las claves conocidas (las de ``base``) de ``datos``."""
    resultado = {}
    for clave, valor_pred in base.items():
        if clave not in datos:
            continue
        valor = datos[clave]
        if isinstance(valor_pred, dict) and isinstance(valor, dict):
            resultado[clave] = _filtro_claves(valor_pred, valor)
        else:
            resultado[clave] = valor
    return resultado


class Configuracion:
    """Contenedor de la configuracion completa de la app."""

    def __init__(self, datos: dict):
        self.datos = datos

    @classmethod
    def cargar(cls, ruta: Path | None = None) -> "Configuracion":
        ruta = Path(ruta or RUTA_CONFIG)
        extra = {}
        if ruta.exists():
            with ruta.open("rb") as f:
                extra = tomllib.load(f)
        datos = _fusionar(CONFIG_DEFECTO, extra)
        # Limpiar pads invalidos y normalizar
        pads = []
        for p in datos.get("soundboard", {}).get("pads", []):
            if isinstance(p, dict) and p.get("nombre"):
                p.setdefault("archivo", "")
                p.setdefault("imagen", "")
                p.setdefault("color", "#43a047")
                p.setdefault("modo", "global")
                pads.append(p)
        datos.setdefault("soundboard", {})["pads"] = pads
        return cls(datos)

    # -- secciones ---------------------------------------------------------

    @property
    def conexion(self) -> dict:
        return self.datos["conexion"]

    @property
    def reproduccion(self) -> dict:
        return self.datos["reproduccion"]

    @property
    def interfaz(self) -> dict:
        return self.datos["interfaz"]

    @property
    def soundboard(self) -> dict:
        return self.datos["soundboard"]

    @property
    def pads(self) -> list[dict]:
        return self.soundboard["pads"]

    # -- metodos de lectura/escritura de config.toml -----------------------

    def guardar(self, ruta: Path | None = None) -> None:
        datos = _filtro_claves(CONFIG_DEFECTO, self.datos)
        # Conservar pads completos (no filtrados)
        datos.setdefault("soundboard", {})["pads"] = copy.deepcopy(self.pads)

        # TOML no permite claves vacias como nombre de seccion: asegurar
        # que los pads no tengan propiedades vacias problematicas
        for p in datos["soundboard"]["pads"]:
            for k in ("nombre",):
                if not p.get(k):
                    raise ValueError("Todos los pads deben tener nombre.")

        ruta = ruta or RUTA_CONFIG
        ruta = Path(ruta)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with ruta.open("wb") as f:
            tomli_w.dump(datos, f)

    # -- pads --------------------------------------------------------------

    def obtener_pad(self, indice: int) -> dict | None:
        if 0 <= indice < len(self.pads):
            return self.pads[indice]
        return None

    def agregar_pad(self, pad: dict) -> int:
        self.pads.append(pad)
        return len(self.pads) - 1

    def eliminar_pad(self, indice: int) -> None:
        if 0 <= indice < len(self.pads):
            del self.pads[indice]

    def intercambiar_pads(self, a: int, b: int) -> None:
        pa, pb = self.pads[a], self.pads[b]
        self.pads[a], self.pads[b] = pb, pa

    def actualizar_pad(self, indice: int, cambios: dict) -> None:
        if 0 <= indice < len(self.pads):
            self.pads[indice].update(cambios)


def tamano_pad_px(tamano: str) -> int:
    return EXT_TAMANO_PAD.get(tamano, EXT_TAMANO_PAD["mediano"])