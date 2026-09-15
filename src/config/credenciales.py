"""Almacenamiento seguro de la contrasena de OBS.

La contrasena vive en el Windows Credential Manager (Vault) via ``keyring``:
nunca queda en texto plano en ningun archivo. Host y puerto si van en
``config.toml`` (no son secretos).
"""

from __future__ import annotations

import logging

import keyring

log = logging.getLogger(__name__)

_SERVICIO = "ConsolaOBS-v2"
_USUARIO = "obs"


def obtener() -> str | None:
    """Devuelve la contrasena guardada o None si no existe."""
    try:
        valor = keyring.get_password(_SERVICIO, _USUARIO)
        return valor or None
    except Exception:  # keyring puede fallar por backends rotos
        log.exception("No se pudo leer del Vault de Windows")
        return None


def guardar(contrasena: str) -> bool:
    """Guarda la contrasena en el Vault. Devuelve True si tuvo exito."""
    try:
        keyring.set_password(_SERVICIO, _USUARIO, contrasena)
        return True
    except Exception:
        log.exception("No se pudo escribir en el Vault de Windows")
        return False


def borrar() -> None:
    try:
        keyring.delete_password(_SERVICIO, _USUARIO)
    except keyring.errors.PasswordDeleteError:
        pass
    except Exception:
        log.exception("No se pudo borrar la contrasena del Vault")