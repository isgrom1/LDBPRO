"""El archivo _CORREGIDA conserva listas desplegables, fórmulas y estilos."""

from __future__ import annotations

import hashlib
import re
import warnings
import zipfile

import openpyxl
import pytest

from auditor import xlsx_xml
from auditor.formato import soffice_disponible


def _sha(ruta):
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


@pytest.fixture(scope="session")
def corregida(resultado):
    return resultado.archivos["corregida"]


def test_mismo_numero_de_listas_desplegables(planilla, corregida):
    original = xlsx_xml.contar_x14_validaciones(planilla)
    assert sum(original.values()) == 5
    assert xlsx_xml.contar_x14_validaciones(corregida) == original


def test_extlst_de_validaciones_identico(planilla, corregida):
    with zipfile.ZipFile(planilla) as a, zipfile.ZipFile(corregida) as b:
        for parte in xlsx_xml.mapa_hojas(a).values():
            xa, xb = a.read(parte).decode(), b.read(parte).decode()
            ea = re.search(r"<extLst>.*</extLst>", xa, re.S)
            eb = re.search(r"<extLst>.*</extLst>", xb, re.S)
            assert (ea and ea.group()) == (eb and eb.group())


def test_formulas_y_estilos_intactos(planilla, corregida):
    assert xlsx_xml.contar_formulas(corregida) == xlsx_xml.contar_formulas(planilla)
    with zipfile.ZipFile(planilla) as a, zipfile.ZipFile(corregida) as b:
        assert a.namelist() == b.namelist()
        assert a.read("xl/styles.xml") == b.read("xl/styles.xml")
        assert a.read("xl/sharedStrings.xml") == b.read("xl/sharedStrings.xml")
        assert 'fullCalcOnLoad="1"' in b.read("xl/workbook.xml").decode()
        cambiadas = [n for n in a.namelist() if a.read(n) != b.read(n)]
        # Estaciones, Registros y 'validación datos' (T01 corrige la lista de la plantilla)
        assert set(cambiadas) == {
            "xl/workbook.xml", "xl/worksheets/sheet2.xml", "xl/worksheets/sheet3.xml", "xl/worksheets/sheet6.xml",
        }


def test_solo_cambian_las_celdas_auto_corregidas(resultado):
    v = resultado.verificacion
    assert v.celdas_ok and len(v.celdas_distintas) == len(resultado.cambios) == 128


def test_valores_corregidos(planilla, corregida):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(corregida)
        wb_original = openpyxl.load_workbook(planilla)
    est, reg = wb["Estaciones"], wb["Registros"]
    assert est["K19"].value == 251835 and est["L20"].value == 6539767
    assert est["Q3"].value == 1 and est["R3"].value == 0 and est["T15"].value == 1  # 'SI' -> 1
    assert reg["AD171"].value == "Cometocino de Gay" and reg["AD205"].value == "Lagartija lemniscata"
    assert reg["AE171"].value.startswith("=INDEX(")  # la fórmula sigue ahí
    assert reg["AN64"].value == 6539822
    for ref in ("K19", "Q3", "R3"):  # conserva el estilo de cada celda
        assert est[ref].style_id == wb_original["Estaciones"][ref].style_id
    assert reg["AD171"].style_id == wb_original["Registros"]["AD171"].style_id


def test_el_original_no_se_toca(planilla, tmp_path_factory):
    from auditor.cli import ejecutar

    antes = _sha(planilla)
    ejecutar(planilla, salida=tmp_path_factory.mktemp("otra"), con_libreoffice=False, informar=lambda *a: None)
    assert _sha(planilla) == antes
    with pytest.raises(xlsx_xml.ErrorEdicionXML):
        xlsx_xml.escribir_con_cambios(planilla, planilla, {})


def test_reauditar_la_corregida_da_lo_mismo_que_en_memoria(resultado):
    assert resultado.verificacion.reauditoria_coincide, resultado.verificacion.diferencias_reauditoria


def test_despues_de_corregir(resultado):
    p = resultado.puntaje_despues
    assert set(p.reglas_criticas) == {"E02", "E06", "R02", "R06"}
    assert p.indice > resultado.puntaje_antes.indice
    for regla in ("E05", "E11", "R03", "R05", "R22"):
        assert not [h for h in resultado.hallazgos_despues if h.id_regla == regla], regla


@pytest.mark.libreoffice
@pytest.mark.skipif(not soffice_disponible(), reason="LibreOffice no está instalado")
def test_libreoffice_recalcula_sin_errores_nuevos(resultado):
    lo = resultado.verificacion.libreoffice
    assert lo is not None
    assert lo["errores_nuevos"] == []
    assert lo["errores_corregida"] <= lo["errores_original"]
    # Las uniones que rehace el auditor coinciden con lo que calculan las fórmulas
    assert lo["diferencias_uniones"] == []
    assert resultado.verificacion.todo_ok
