"""Fixtures compartidas.

La planilla de prueba NO se versiona (repositorio público; tiene coordenadas de especies y
datos del proyecto). Ponla en tests/datos/ o indica su ruta en la variable PLANILLA_XPE.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

from auditor.cli import ejecutar
from auditor.config import cargar_config
from auditor.formato import soffice_disponible
from auditor.modelo import leer_libro

DATOS = Path(__file__).parent / "datos"
NOMBRE = "Formato_Planilla_Fauna_XPE_Externo_v4_version_1.xlsb.xlsx"
HOY = date(2026, 10, 24)


def _buscar_planilla() -> Path | None:
    if os.environ.get("PLANILLA_XPE"):
        return Path(os.environ["PLANILLA_XPE"])
    if (DATOS / NOMBRE).exists():
        return DATOS / NOMBRE
    return next(iter(sorted(DATOS.glob("*.xlsx"))), None)


@pytest.fixture(scope="session")
def planilla() -> Path:
    ruta = _buscar_planilla()
    if ruta is None or not ruta.exists():
        pytest.skip(f"Falta la planilla de prueba: cópiala a {DATOS}/{NOMBRE} o define PLANILLA_XPE")
    return ruta


@pytest.fixture(scope="session")
def config() -> dict:
    return cargar_config()


@pytest.fixture(scope="session")
def libro(planilla, config):
    return leer_libro(planilla, config)


@pytest.fixture(scope="session")
def resultado(planilla, tmp_path_factory):
    """Corrida completa sobre la planilla adjunta (con LibreOffice si está instalado)."""
    salida = tmp_path_factory.mktemp("auditoria")
    return ejecutar(planilla, salida=salida, con_libreoffice=bool(soffice_disponible()), hoy=HOY,
                    informar=lambda *a: None)


@pytest.fixture(scope="session")
def escenario(planilla, tmp_path_factory) -> Path:
    from escenario import crear_escenario

    return crear_escenario(planilla, tmp_path_factory.mktemp("escenario") / "escenario_aceptacion.xlsx")


@pytest.fixture(scope="session")
def resultado_escenario(escenario, tmp_path_factory):
    salida = tmp_path_factory.mktemp("auditoria_escenario")
    return ejecutar(escenario, salida=salida, con_libreoffice=False, hoy=HOY, informar=lambda *a: None)


def hallazgos(res, regla=None, **filtros):
    return [
        h for h in res.hallazgos_antes
        if (regla is None or h.id_regla == regla) and all(getattr(h, k) == v for k, v in filtros.items())
    ]
