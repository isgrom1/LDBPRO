"""Reglas sobre la planilla adjunta: lo que se sabe que tiene, y lo que no debe marcar."""

from __future__ import annotations

from collections import Counter

from conftest import hallazgos

from auditor.hallazgos import REGLAS, Severidad
from auditor.motor import ID_ERROR


def celdas(res, regla, **f):
    return sorted(h.celda for h in hallazgos(res, regla, **f))


def test_ninguna_regla_falla(resultado):
    assert not hallazgos(resultado, ID_ERROR)


def test_todos_los_hallazgos_tienen_los_campos_pedidos(resultado):
    for h in resultado.hallazgos_antes:
        assert h.id_regla in REGLAS and h.hoja and h.problema and h.sugerencia
        assert isinstance(h.severidad, Severidad)
        assert h.auto_corregible == (h.correccion is not None)
        if h.auto_corregible:
            assert REGLAS[h.id_regla].auto_corregible


def test_estaciones(resultado):
    assert celdas(resultado, "E02") == ["F19", "F20", "F21", "F22", "F23", "F24"]
    assert celdas(resultado, "E03") == ["F15"]  # 'Medio alto' -> 'Matorral medio-alto'; NO 'Matorral medio-bajo'
    assert celdas(resultado, "E05") == ["K19", "K23", "L20"]
    assert celdas(resultado, "E06") == ["K24", "L24", "M24", "N24", "O24", "P24"]  # PMF48 sin coordenadas
    assert [h.valor_actual for h in hallazgos(resultado, "E08")] == ["72 m"]  # PMF55
    assert len(hallazgos(resultado, "E11")) == 22 * 5
    assert sorted(h.id_estacion for h in hallazgos(resultado, "E12")) == ["PMF38", "PMF41", "PMF50"]
    # En esta versión del archivo no hay: ID Final vacío, coordenadas copiadas, typos de proyecto
    for regla in ("E01", "E04", "E07", "E09", "E10", "E13"):
        assert not hallazgos(resultado, regla), regla


def test_correcciones_de_coordenadas_y_metodologias(resultado):
    por_celda = {(h.hoja, h.celda): h.correccion.valor_nuevo for h in resultado.hallazgos_antes if h.correccion}
    assert por_celda[("Estaciones", "K19")] == 251835
    assert por_celda[("Estaciones", "Q3")] == 1  # 'Si'
    assert por_celda[("Estaciones", "R3")] == 0  # vacío
    assert por_celda[("Registros", "AN64")] == 6539822  # captura con U+00A0 (R22)


def test_nombres(resultado):
    assert celdas(resultado, "R02") == ["AD13"] and celdas(resultado, "R04") == ["AD13"]
    r03 = {h.celda: h.correccion.valor_nuevo for h in hallazgos(resultado, "R03")}
    assert r03["AD171"] == "Cometocino de Gay" and r03["AD12"] == "Lagarto de Zapallar"
    assert r03["AD174"] == r03["AD205"] == "Lagartija lemniscata"
    assert len(r03) == 9
    assert celdas(resultado, "R05") == ["AD174", "AD205"]
    assert "Sapo de Atacama" in hallazgos(resultado, "R02")[0].sugerencia  # congénere presente en la base


def test_registros(resultado):
    assert celdas(resultado, "R06") == ["AI196"]  # Cururo PMF18 sin abundancia
    assert celdas(resultado, "R08") == ["AD198"]  # Equus caballus PMF17
    assert "Código Civil (art. 608)" in hallazgos(resultado, "R08")[0].sugerencia  # «Caballo feral»
    assert celdas(resultado, "R10") == ["Z124", "Z196"]
    # Macho y hembra en la misma estación y hora son dos individuos, no un duplicado (filas 172/173)
    assert "AD173" not in celdas(resultado, "R15")
    r15 = {h.celda: h for h in hallazgos(resultado, "R15")}
    assert set(r15) == {"AD99", "AD195"}  # solo cambia el comportamiento / visto y oído
    assert r15["AD99"].severidad == Severidad.INFO
    assert r15["AD99"].comparacion == [("", "Fila 98", "Fila 99"), ("Comportamiento", "Volando", "Posado")]
    assert "oído y visto" in r15["AD195"].problema
    assert celdas(resultado, "R17") == ["AK10", "AK11", "AL13"]
    assert "AK51" in celdas(resultado, "R18")  # 'TC_01812-TC_01813'
    assert celdas(resultado, "R20") == ["AA124"]  # cámara trampa no marcada en PMF37
    assert celdas(resultado, "R23") == ["AL121"]  # 'En sherman' en un registro de transecto
    r11 = hallazgos(resultado, "R11")
    assert r11 == [] and resultado.estadisticas["R11"]["Registros"]["pct"] > 80  # solo indicador
    assert hallazgos(resultado, "R07") == []  # 12 chincoles: paseriforme, ave de bandada
    for regla in ("R01", "R09", "R12", "R13", "R14", "R16", "R19", "R24", "X01", "X02"):
        assert not hallazgos(resultado, regla), regla


def test_plantilla(resultado):
    # T01 corrige la celda de la lista desplegable ('validación datos'), no la del registro
    t01 = {h.valor_actual: h.correccion.valor_nuevo for h in hallazgos(resultado, "T01")}
    assert t01 == {"Huella": "Huellas", "Vocalizando": "Vocalización"}
    clases = {h.valor_actual for h in hallazgos(resultado, "B01") if h.celda.startswith("E")}
    assert clases == {"Mammalia", "Reptilia"}


def test_severidades(resultado):
    sev = Counter(h.severidad for h in resultado.hallazgos_antes)
    assert sev[Severidad.CRITICO] == 26
    assert set(resultado.puntaje_antes.reglas_criticas) == {"E02", "E05", "E06", "R02", "R03", "R06"}
