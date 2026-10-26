"""Motor de la versión web: el mismo enrutador del servidor local, dentro del navegador.

Corre en Pyodide (Python en WebAssembly) en un Web Worker, así que no hay hilos: el análisis y
la generación quedan en cola y los ejecuta trabajar(), avisando cada paso con avisar(json).
La planilla nunca sale del navegador.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..config import cargar_config
from .servidor import Aplicacion, atender

_app: Aplicacion | None = None


def iniciar(carpeta: str, config: str | None, avisar) -> None:
    """avisar(texto_json): manda al navegador el estado de una sesión mientras se trabaja."""
    global _app
    _app = Aplicacion(Path(carpeta), cargar_config(config), con_libreoffice=False)
    _app.web, _app.hilos = True, False
    _app.al_avanzar = lambda s: avisar(json.dumps({"tipo": "estado", "id": s.id, "resumen": s.resumen()},
                                                  ensure_ascii=False, default=str))


def pedir(metodo: str, ruta: str, cabeceras_json: str, cuerpo) -> tuple:
    """(estado, tipo, nombre de descarga, cuerpo, hay trabajo pendiente).

    cuerpo llega como Uint8Array de JavaScript (JsProxy), bytes o None.
    """
    if hasattr(cuerpo, "to_bytes"):
        cuerpo = cuerpo.to_bytes()
    r = atender(_app, metodo, ruta, json.loads(cabeceras_json or "{}"), bytes(cuerpo or b""))
    return r.estado, r.tipo, r.descarga, r.cuerpo, bool(_app.pendientes)


def trabajar() -> bool:
    return _app.ejecutar_pendientes()
