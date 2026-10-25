"""Prueba de aceptación: los errores de la revisión manual tienen que aparecer.

En la planilla adjunta, cada situación que existe en los datos tiene que estar detectada. Las
que no existen en esta versión del archivo (PMF20, PMF29, PMF32, PMF35, ID Final vacío…) se
inyectan en una copia (tests/escenario.py) y ahí tienen que detectarse todas.
"""

from __future__ import annotations

import pytest
from aceptacion import (
    AUSENTE,
    DETECTADO,
    ESPERADOS,
    FALLA,
    PARCIAL,
    evaluar,
    tabla_markdown,
)
from conftest import hallazgos


def test_planilla_adjunta_detecta_todo_lo_que_contiene(resultado):
    filas = evaluar(resultado)
    assert [f for f in filas if f[2] == FALLA] == []
    estados = {desc: estado for desc, _, estado, _ in filas}
    # Lo que de verdad está en este archivo
    for desc in (
        "Rhinella spp. no está en nombres BD y da #N/A",
        "«lagartija lemniscata» en minúscula vs «Lagartija lemniscata»",
        "«Cometocino de gay» y «Lagarto de zapallar» difieren solo en mayúsculas",
        "Coordenadas con U+00A0 en PMF38, PMF40 y PMF55",
        "Equus caballus en PMF17",
        "La mayoría de los registros de aves son entre 12 y 17 h",
    ):
        assert estados[desc] == DETECTADO, desc
    # Lo que esta versión del archivo trae distinto a la revisión manual
    assert estados["Cururo con abundancia «P» en PMF18 y PMF29"] == PARCIAL  # PMF18 vacío, no hay PMF29
    assert estados["PMF35 con transecto de ~36 m y PMF55 con ~72 m"] == PARCIAL  # no hay PMF35
    for desc in (
        "PMF20 está en Registros pero no existe en Estaciones",
        "PMF29: fin idéntico al de PMF32 y largo ~505 m",
        "ID Final Estación vacío en todas las estaciones",
    ):
        assert estados[desc] == AUSENTE, desc


def test_escenario_detecta_todos_los_esperados(resultado_escenario):
    filas = evaluar(resultado_escenario)
    assert len(filas) == len(ESPERADOS)
    no_detectados = [(d, e, det) for d, _, e, det in filas if e != DETECTADO]
    assert no_detectados == []


def test_escenario_auto_corrige_id_final_y_proyecto(resultado_escenario):
    cambios = {(c.hoja, c.celda): (c.valor_nuevo, c.reglas) for c in resultado_escenario.cambios}
    assert cambios[("Estaciones", "I3")] == ("PMF49", ["E04"])
    assert cambios[("Estaciones", "B24")] == ("Punta del Sol", ["E13"])
    assert ("Estaciones", "B3") not in cambios


def test_escenario_audita_registros_ta_cuando_tiene_datos(resultado_escenario):
    assert "Registros TA" in [t.hoja for t in resultado_escenario.libro.registros]
    assert [h.celda for h in hallazgos(resultado_escenario, "R01", hoja="Registros TA")] == ["L10"]


def test_escenario_detecta_caches_desactualizados(resultado_escenario):
    # Las celdas se editaron fuera de Excel: las fórmulas de Registros guardan valores viejos.
    x02 = hallazgos(resultado_escenario, "X02")
    assert any(h.celda == "M10" for h in x02)  # ID Final copiado desde Estaciones!I
    # ...y aun así las uniones rehechas en código usan los datos actuales:
    assert not [h for h in resultado_escenario.hallazgos_despues if h.id_regla == "X02"]


def test_tabla_esperado_vs_detectado(resultado, resultado_escenario):
    tabla = tabla_markdown(resultado, resultado_escenario)
    assert tabla.count("\n") == len(ESPERADOS) + 1
    assert FALLA not in tabla


@pytest.mark.parametrize("desc, reglas, _fn", ESPERADOS)
def test_cada_esperado_tiene_regla_registrada(desc, reglas, _fn):
    from auditor.hallazgos import REGLAS

    for r in reglas.replace("+", "/").split("/"):
        assert r in REGLAS, (desc, r)
