"""Pruebas unitarias que no necesitan la planilla."""

from __future__ import annotations

import zipfile
from datetime import date, time

import pytest

from auditor import xlsx_xml
from auditor.config import cargar_config
from auditor.formato import detectar_formato, nombre_base
from auditor.hallazgos import Hallazgo, Severidad
from auditor.motor import (
    ESTADO_LISTA,
    ESTADO_NO_USAR,
    ESTADO_REVISION,
    calcular_puntaje,
)
from auditor.reglas.estaciones import _grupos_similares
from auditor.texto import (
    a_fecha,
    a_hora,
    a_numero,
    clave,
    describir_espacios,
    en_rango_horario,
    limpiar,
    mostrar,
)


# --------------------------------------------------------------------------- texto
def test_limpiar_y_clave_quitan_invisibles():
    assert limpiar(" Cometocino  de Gay ") == "Cometocino de Gay"
    assert clave("Cometocino de gay") == clave("COMETOCINO DE GAY ")


def test_describir_espacios():
    assert describir_espacios(" 251835") == ["espacio de no separación (U+00A0) al inicio"]
    assert "espacios dobles" in describir_espacios("Sapo  de rulo")
    assert describir_espacios("Loica") == []


@pytest.mark.parametrize("valor, esperado", [
    (" 251835", 251835), ("6.539.767", 6539767), ("12,5", 12.5), (" 3 ", 3), (7, 7),
    ("P", None), ("", None), (None, None), (True, None),
])
def test_a_numero(valor, esperado):
    assert a_numero(valor) == esperado


def test_fechas_y_horas():
    assert a_fecha(46260) == date(2026, 8, 26)
    assert a_fecha("29/08/2026") == date(2026, 8, 29)
    assert a_hora("13:37") == time(13, 37)
    assert a_hora(0.5) == time(12, 0)
    assert a_hora("sin hora") is None
    assert en_rango_horario(time(23, 0), time(19, 0), time(6, 0))
    assert not en_rango_horario(time(12, 0), time(19, 0), time(6, 0))


def test_mostrar_deja_visible_el_nbsp():
    assert mostrar(" 251835") == "[U+00A0]251835"
    assert mostrar(None) == "(vacío)"


# --------------------------------------------------------------------------- formato
def test_detectar_formato_por_contenido(tmp_path):
    xlsb = tmp_path / "planilla.xlsb.xlsx"  # extensión engañosa
    with zipfile.ZipFile(xlsb, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/workbook.bin", b"\x00")
    assert detectar_formato(xlsb) == "xlsb"
    xls = tmp_path / "vieja.xlsx"
    xls.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100)
    assert detectar_formato(xls) == "xls"
    txt = tmp_path / "no.xlsx"
    txt.write_text("hola")
    assert detectar_formato(txt) == "desconocido"


def test_nombre_base():
    assert nombre_base("a/Formato_v4_version_1.xlsb.xlsx") == "Formato_v4_version_1"
    assert nombre_base("x.XLSX") == "x"


# --------------------------------------------------------------------------- XML
HOJA = (
    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<cols><col min="3" max="3" style="7" width="9"/></cols><sheetData>'
    '<row r="1"><c r="A1" s="2" t="s"><v>0</v></c><c r="B1" s="3"><f>A1*2</f><v>2</v></c>'
    '<c r="D1" s="4"/></row>'
    '<row r="3" spans="1:4"/>'
    '<row r="5"><c r="A5"><v>1</v></c></row>'
    '</sheetData><extLst><x14:dataValidations count="1"/></extLst></worksheet>'
)


def test_reemplaza_celda_conservando_estilo():
    xml = xlsx_xml.aplicar_cambios_hoja(HOJA, {"A1": 251835, "D1": "Cometocino de Gay & <cía>"})
    assert '<c r="A1" s="2"><v>251835</v></c>' in xml
    assert '<c r="D1" s="4" t="inlineStr"><is><t xml:space="preserve">Cometocino de Gay &amp; &lt;cía&gt;</t></is></c>' in xml
    assert "<extLst><x14:dataValidations count=\"1\"/></extLst>" in xml


def test_inserta_celdas_y_filas_en_orden():
    xml = xlsx_xml.aplicar_cambios_hoja(HOJA, {"C1": 0, "B3": 1, "A4": "x", "A9": 2})
    assert xml.index('r="B1"') < xml.index('r="C1"') < xml.index('r="D1"')
    assert '<c r="C1" s="7"><v>0</v></c>' in xml  # toma el estilo de la columna
    assert '<row r="3" spans="1:4"><c r="B3"><v>1</v></c></row>' in xml
    assert xml.index('<row r="3"') < xml.index('<row r="4"') < xml.index('<row r="5"') < xml.index('<row r="9"')


def test_no_sobrescribe_formulas():
    with pytest.raises(xlsx_xml.ErrorEdicionXML):
        xlsx_xml.aplicar_cambios_hoja(HOJA, {"B1": 5})


def test_activar_recalculo():
    assert 'fullCalcOnLoad="1"' in xlsx_xml.activar_recalculo('<workbook><calcPr calcId="191029"/></workbook>')
    assert xlsx_xml.activar_recalculo('<workbook><calcPr fullCalcOnLoad="0"/></workbook>').count("fullCalcOnLoad") == 1
    assert xlsx_xml.activar_recalculo("<workbook><sheets/></workbook>") == (
        '<workbook><sheets/><calcPr fullCalcOnLoad="1"/></workbook>'
    )


# --------------------------------------------------------------------------- reglas
def test_variantes_de_ambiente():
    grupos = _grupos_similares([
        "Matorral bajo", "MATORRAL BAJO ", "Matoral bajo", "Matorral medio-bajo", "Matorral medio-alto",
        "Medio alto", "Bosque esclerófilo", "Bosque esclerofilo", "Matorral medio / Quebrada",
    ])
    assert {"Matorral bajo", "MATORRAL BAJO ", "Matoral bajo"} in grupos
    assert {"Matorral medio-alto", "Medio alto"} in grupos
    assert {"Bosque esclerófilo", "Bosque esclerofilo"} in grupos
    assert not any("Matorral medio-bajo" in g for g in grupos)


def _h(regla, sev, hoja, fila):
    return Hallazgo(regla, hoja, f"A{fila}", fila, None, None, "", "", sev)


def test_puntaje_y_estado():
    class T:
        def __init__(self, hoja, filas):
            self.hoja, self.filas_datos = hoja, filas

    class L:
        estaciones = T("Estaciones", [3, 4])
        registros = [T("Registros", list(range(10, 20)))]

    cfg = cargar_config()
    p = calcular_puntaje(L, [], cfg)
    assert (p.indice, p.estado) == (100, ESTADO_LISTA)
    p = calcular_puntaje(L, [_h("R15", Severidad.ADVERTENCIA, "Registros", 10), _h("R07", Severidad.INFO, "Registros", 11)], cfg)
    assert p.pct_registros == 90 and p.estado == ESTADO_REVISION and p.indice == pytest.approx(0.7 * 90 + 30)
    hs = [_h("E02", Severidad.CRITICO, "Estaciones", 3), _h("R01", Severidad.CRITICO, "Registros", 12),
          _h("R01", Severidad.CRITICO, "Registros", 13)]
    p = calcular_puntaje(L, hs, cfg)
    # 80% registros, 50% estaciones, 2 reglas críticas distintas
    assert p.indice == pytest.approx(0.7 * 80 + 0.3 * 50 - 4)
    assert p.estado == ESTADO_NO_USAR and p.reglas_criticas == ["E02", "R01"]
