"""La estructura de la planilla se verifica al abrirla (no se adivina)."""

from __future__ import annotations

from auditor.formato import detectar_formato
from auditor.modelo import HOJA_TA


def test_formato_real_de_un_xlsb_xlsx(planilla):
    assert planilla.name.endswith(".xlsb.xlsx")
    assert detectar_formato(planilla) == "xlsx"


def test_mapeo_estaciones(libro):
    est = libro.estaciones
    assert est.fila_encabezado == 2
    esperado = {
        "ambiente": "F", "ladera": "G", "id": "H", "id_final": "I", "utm_e": "K", "utm_s": "L",
        "utm_e_ini": "M", "utm_s_ini": "N", "utm_e_fin": "O", "utm_s_fin": "P", "met_transecto": "Q",
        "met_pb_aves": "R", "met_pb_anfibios": "S", "met_camara": "T", "met_sherman": "U",
    }
    assert {k: est.columnas[k] for k in esperado} == esperado
    assert len(est.filas_datos) == 22 and est.filas_datos[0] == 3


def test_mapeo_registros(libro):
    reg = libro.registros[0]
    assert reg.hoja == "Registros" and reg.fila_encabezado == 9
    esperado = {
        "id": "L", "fecha": "D", "hora": "Z", "metodologia": "AA", "dir_ind": "AB", "tipo_registro": "AC",
        "nombre_comun": "AD", "nombre_cientifico": "AE", "abundancia": "AI", "fotos": "AK",
        "observaciones": "AL", "utm_e_captura": "AM", "utm_s_captura": "AN", "clase": "AQ",
    }
    assert {k: reg.columnas[k] for k in esperado} == esperado
    # Una fila es registro solo si tiene ID Estación (la plantilla trae fórmulas hasta la fila 832)
    assert len(reg.filas_datos) == 198 and reg.filas_datos[0] == 10 and reg.filas_datos[-1] == 207
    assert {"nombre_cientifico", "clase", "ambiente"} <= reg.columnas_naranjas()
    assert "comportamiento" not in reg.columnas_naranjas()  # blanca con fórmula de ayuda


def test_registros_ta_sin_datos_no_se_audita(libro):
    assert libro.registros_sin_datos == [HOJA_TA]


def test_nombres_bd(libro):
    bd = libro.nombres_bd
    assert (bd.columnas["nombre_comun"], bd.columnas["nombre_cientifico"], bd.columnas["clase"]) == ("A", "B", "E")
    assert len(bd.filas_datos) == 889


def test_listas_desde_validacion_datos(libro):
    assert "Transecto" in libro.listas["metodologia"] and "Songmeter o similar" in libro.listas["metodologia"]
    assert libro.listas["tipo_registro"][:2] == ["Individuo", "Vocalización"] and "Huella" in libro.listas["tipo_registro"]
    assert "Plano" in libro.listas["ladera"]
    assert "Vocalizando" in libro.listas["comportamiento"] and "Agrupación" not in libro.listas["comportamiento"]
    assert "validación datos" in libro.origen_listas["metodologia"]


def test_directo_indirecto_se_lee_de_la_formula(libro):
    assert libro.directo_indirecto["Directo"] == ["Individuo", "Vocalización"]
    assert "Huellas" in libro.directo_indirecto["Indirecto"]
    assert libro.origen_directo_indirecto.startswith("fórmula de Registros!AB")
