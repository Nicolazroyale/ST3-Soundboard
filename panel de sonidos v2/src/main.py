"""ConsolaOBS v2 — Panel de audio para OBS Studio.

Ejecutar con:
    python -m src.main
"""

import sys
from pathlib import Path

# Asegurar que la raiz del proyecto esta en sys.path para imports relativos
raiz = Path(__file__).resolve().parents[1]
if str(raiz) not in sys.path:
    sys.path.insert(0, str(raiz))

from src.app import iniciar_app

if __name__ == "__main__":
    iniciar_app()