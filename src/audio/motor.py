"""Motor de reproduccion abstracto."""

from __future__ import annotations

from enum import Enum
from typing import Protocol


class ModoReproduccion(Enum):
    OBS = "obs"
    LOCAL = "local"


class MotorBase(Protocol):
    """Interfaz comun para motores de reproduccion."""

    nombre: str

    def reproducir(self, archivo: str, pad_id: str = "0") -> None: ...
    def detener(self, pad_id: str = "0") -> None: ...
    def reiniciar(self, archivo: str, pad_id: str = "0") -> None: ...
    def detener_todo(self) -> None: ...

    # -- hooks de ciclo de vida del motor ------------------------------------

    def inicializar(self, cliente_obs=None) -> None:
        """Se llama una vez que el motor se usa por primera vez."""

    def limpiar(self) -> None:
        """Se llama al cerrar la app o al cambiar de motor."""