"""Lectura de la planilla XPE a DataFrames.

Las columnas se ubican por el texto del encabezado (no por la letra) y se verifica la fila
de encabezados al abrir. De cada celda se guarda el valor crudo; en las columnas con
fórmula se guarda además la fórmula y su valor cacheado, que NO se usa para auditar
(las uniones se rehacen en código, ver contexto.py).
"""

from __future__ import annotations

import copy
import re
import warnings
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
import pandas as pd
from openpyxl.utils import column_index_from_string, get_column_letter

from . import xlsx_xml
from .texto import clave_flexible, es_vacio, limpiar

HOJA_ESTACIONES = "Estaciones"
HOJA_REGISTROS = "Registros"
HOJA_TA = "Registros TA"
HOJA_BD = "nombres BD"
HOJA_VALIDACION = "validación datos"

# clave lógica -> alias del encabezado (exacto, o prefijo si termina en '*')
_COORDS = {
    "utm_e": ["UTM E Estación (X)"],
    "utm_s": ["UTM S Estación (Y)"],
    "utm_e_ini": ["UTM E inicio (X)"],
    "utm_s_ini": ["UTM S Inicio (Y)"],
    "utm_e_fin": ["UTM E fin (X)"],
    "utm_s_fin": ["UTM S fin (Y)"],
}
_METODOLOGIAS = {
    "met_transecto": ["Transecto general"],
    "met_pb_aves": ["Playback Aves"],
    "met_pb_anfibios": ["Playback Anfibios"],
    "met_camara": ["Cámara Trampa"],
    "met_sherman": ["Trampas Sherman"],
}
_DESCRIPCION = {
    "proyecto": ["Proyecto"],
    "region": ["Región"],
    "temporada": ["Temporada"],
    "ambiente": ["Ambiente"],
    "ladera": ["Ladera de Exposición", "Ladera*"],
}
_TAXONOMIA = {
    "reino": ["Reino"],
    "filo": ["Filo"],
    "clase": ["Clase"],
    "orden": ["Orden"],
    "familia": ["Familia"],
    "genero": ["Género"],
    "epiteto": ["Epíteto específico"],
}
_PERSONAS = {
    "muestreado_por": ["Muestreado por*"],
    "identificado_por": ["Identificado por*"],
}
_COMUNES_REGISTROS = {
    "n": ["N°", "N"],
    **_DESCRIPCION,
    "fecha": ["Fecha"],
    "clima": ["Clima"],
    "id": ["ID Estación"],
    "id_final": ["ID Final Estación", "ID Estación final"],
    "utm_e": ["UTM E Estación (X)"],
    "utm_s": ["UTM S Estación (Y)"],
    "ampm": ["AM/PM"],
    "hora": ["Hora"],
    "nombre_comun": ["Nombre común"],
    "nombre_cientifico": ["Nombre científico"],
    "estado_desarrollo": ["Estado desarrollo"],
    "sexo": ["Sexo"],
    "abundancia": ["Abundancia"],
    "comportamiento": ["Comportamiento"],
    "fotos": ["Fotos"],
    "observaciones": ["Observaciones"],
    **_TAXONOMIA,
    **_PERSONAS,
}

COLUMNAS: dict[str, dict[str, list[str]]] = {
    HOJA_ESTACIONES: {
        "n": ["N"],
        **_DESCRIPCION,
        "id": ["ID Estación"],
        "id_final": ["ID Final Estación"],
        **_COORDS,
        **_METODOLOGIAS,
        **_PERSONAS,
    },
    HOJA_REGISTROS: {
        **_COMUNES_REGISTROS,
        **{k: v for k, v in _COORDS.items() if k.endswith(("_ini", "_fin"))},
        **_METODOLOGIAS,
        "metodologia": ["Metodología usada para registro", "Metodología*"],
        "dir_ind": ["Tipo registro: Directo/Indirecto", "Tipo registro*"],
        "tipo_registro": ["Registro: Individuo, feca, vocalización, etc", "Registro:*"],
        "estado_organismo": ["Estado del organismo"],
        "utm_e_captura": ["UTM E Captura (X)", "UTM E Captura*"],
        "utm_s_captura": ["UTM S Captura (Y)", "UTM S Captura*"],
    },
    HOJA_TA: {
        **_COMUNES_REGISTROS,
        "origen": ["Origen"],
        "destino": ["Destino"],
        "altura": ["Altura vuelo"],
        "altura_lat": ["Altura vuelo (LAT o metros)"],
    },
    HOJA_BD: {
        "nombre_comun": ["Nombre común (1)", "Nombre común*"],
        "nombre_cientifico": ["Nombre científico (2)", "Nombre científico*"],
        "reino": ["Reino*"],
        "tipo": ["Tipo*"],
        "clase": ["Clase*"],
        "orden": ["Orden*"],
        "familia": ["Familia*"],
        "genero": ["Género*"],
        "epiteto": ["Epíteto específico*"],
        "subespecie": ["sub especie*"],
    },
}

OBLIGATORIAS = {
    HOJA_ESTACIONES: ["id", "ambiente", *_COORDS],
    HOJA_REGISTROS: ["id", "fecha", "hora", "metodologia", "tipo_registro", "nombre_comun", "abundancia"],
    HOJA_TA: ["id", "nombre_comun"],
    HOJA_BD: ["nombre_comun", "nombre_cientifico"],
}

# Columnas naranjas de la plantilla (fórmulas INDEX/MATCH o derivadas). Directo/Indirecto no
# está: la plantilla permite sobrescribirla a mano cuando la fórmula da "REVISAR".
_NARANJAS_REGISTROS = {
    "proyecto", "region", "temporada", "ambiente", "ladera", "id_final", *_COORDS,
    *_METODOLOGIAS, "ampm", "nombre_cientifico", *_TAXONOMIA, *_PERSONAS,
}
NARANJAS = {HOJA_REGISTROS: _NARANJAS_REGISTROS, HOJA_TA: _NARANJAS_REGISTROS}

# Columnas con fórmula que la plantilla deja sobrescribir a mano: Directo/Indirecto cuando la
# fórmula da "REVISAR", y Comportamiento, cuya fórmula de ayuda solo propone "Vocalización".
SOBRESCRIBIBLES = {"dir_ind", "comportamiento"}

# Columnas que llena el usuario: si tienen datos y falta el ID, el registro se pierde.
COLUMNAS_USUARIO = {
    HOJA_ESTACIONES: ["ambiente", "ladera", *_COORDS],
    HOJA_REGISTROS: ["fecha", "hora", "metodologia", "tipo_registro", "nombre_comun", "abundancia"],
    HOJA_TA: ["fecha", "hora", "nombre_comun", "abundancia"],
}

# Listas de 'validación datos' (respaldo cuando la columna no tiene lista desplegable)
_LISTAS_POR_ENCABEZADO = {
    "metodologia": "Metodología*",
    "comportamiento": "Comportamiento*",
    "ladera": "Ladera*",
    "tipo_registro": "Tipo de registro*",
}

_RANGO_RE = re.compile(r"^'?(.+?)'?!\$?([A-Z]{1,3})\$?(\d+):\$?([A-Z]{1,3})\$?(\d+)$")


class ErrorEstructura(Exception):
    """La planilla no tiene la estructura XPE esperada."""


@dataclass
class Tabla:
    hoja: str
    fila_encabezado: int
    columnas: dict[str, str]  # clave lógica -> letra
    encabezados: dict[str, str]  # clave lógica -> texto del encabezado
    valores: pd.DataFrame  # índice = fila de Excel
    es_formula: pd.DataFrame
    formulas: pd.DataFrame
    filas_datos: list[int]
    filas_sin_id: list[int] = field(default_factory=list)

    def tiene(self, clave: str) -> bool:
        return clave in self.columnas

    def celda(self, fila: int, clave: str) -> str:
        return f"{self.columnas[clave]}{fila}"

    def valor(self, fila: int, clave: str):
        if clave not in self.columnas:
            return None
        return self.valores.at[fila, clave]

    def datos(self) -> pd.DataFrame:
        return self.valores.loc[self.filas_datos]

    def clave_de_letra(self, letra: str) -> str | None:
        return next((k for k, v in self.columnas.items() if v == letra), None)

    def columnas_naranjas(self) -> set[str]:
        """Columnas de fórmula de la plantilla presentes en esta hoja.

        Se exige que sean fórmula en la mayoría de las filas (incluidas las de plantilla) para
        detectarlas aunque alguien haya pegado valores sobre todas las filas con datos.
        """
        if self.es_formula.empty:
            return set()
        frac = self.es_formula.mean()
        return {c for c in NARANJAS.get(self.hoja, set()) if c in self.columnas and frac[c] >= 0.5}

    def columnas_formula(self) -> set[str]:
        """Claves (naranjas) que son fórmula en al menos la mitad de las filas con datos.

        Se mira solo las filas con datos porque la plantilla trae fórmulas de ayuda en
        columnas que llena el usuario (p. ej. Comportamiento = "COMPLETAR").
        """
        filas = self.filas_datos or list(self.es_formula.index)
        if not filas:
            return set()
        frac = self.es_formula.loc[filas].mean()
        return {c for c, f in frac.items() if f >= 0.5}


@dataclass
class Libro:
    ruta: Path  # archivo .xlsx efectivamente leído
    ruta_original: Path
    formato: str
    partes: dict[str, str]
    estaciones: Tabla
    registros: list[Tabla]  # solo hojas de registros con datos
    registros_sin_datos: list[str]
    nombres_bd: Tabla
    listas: dict[str, list[str]]
    origen_listas: dict[str, str]
    listas_refs: dict[str, list[tuple[str, str]]]
    directo_indirecto: dict[str, list[str]]
    origen_directo_indirecto: str
    avisos: list[str] = field(default_factory=list)

    def tablas(self) -> list[Tabla]:
        return [self.estaciones, *self.registros, self.nombres_bd]

    def tabla(self, hoja: str) -> Tabla:
        for t in self.tablas():
            if t.hoja == hoja:
                return t
        raise KeyError(hoja)

    def copia(self) -> Libro:
        return copy.deepcopy(self)


def _coincide(texto, alias: str) -> bool:
    if texto is None:
        return False
    t = clave_flexible(texto)
    if alias.endswith("*"):
        return t.startswith(clave_flexible(alias[:-1]))
    return t == clave_flexible(alias)


def _mapear(fila_valores: list, especif: dict[str, list[str]]) -> tuple[dict, dict]:
    columnas, encabezados, usadas = {}, {}, set()
    for clave, alias in especif.items():
        for a in alias:  # los alias se prueban en orden de preferencia
            for i, texto in enumerate(fila_valores, start=1):
                if i not in usadas and _coincide(texto, a):
                    columnas[clave] = get_column_letter(i)
                    encabezados[clave] = limpiar(texto)
                    usadas.add(i)
                    break
            if clave in columnas:
                break
    return columnas, encabezados


def _leer_tabla(wb_f, wb_v, hoja: str, fila_esperada: int, avisos: list[str]) -> Tabla:
    if hoja not in wb_v.sheetnames:
        raise ErrorEstructura(f"Falta la hoja '{hoja}'")
    especif = COLUMNAS[hoja]
    ws_v, ws_f = wb_v[hoja], wb_f[hoja]

    primeras = [list(r) for r in ws_v.iter_rows(min_row=1, max_row=30, values_only=True)]
    puntajes = [len(_mapear(r, especif)[0]) for r in primeras]
    if not puntajes or max(puntajes) == 0:
        raise ErrorEstructura(f"No se encontraron encabezados en la hoja '{hoja}'")
    mejor = puntajes.index(max(puntajes)) + 1
    fila_enc = fila_esperada
    if fila_esperada > len(puntajes) or puntajes[fila_esperada - 1] < puntajes[mejor - 1]:
        avisos.append(
            f"Hoja '{hoja}': los encabezados están en la fila {mejor}, no en la {fila_esperada} esperada."
        )
        fila_enc = mejor
    columnas, encabezados = _mapear(primeras[fila_enc - 1], especif)
    faltan = [c for c in OBLIGATORIAS[hoja] if c not in columnas]
    if faltan:
        raise ErrorEstructura(
            f"Hoja '{hoja}': faltan columnas obligatorias {faltan} en la fila {fila_enc}"
        )

    claves = list(columnas)
    indices = {k: column_index_from_string(v) for k, v in columnas.items()}
    max_col = max(indices.values())
    filas, vals, esf, frm = [], [], [], []
    filas_f = ws_f.iter_rows(min_row=fila_enc + 1, max_col=max_col)
    filas_v = ws_v.iter_rows(min_row=fila_enc + 1, max_col=max_col)
    for n, (rf, rv) in enumerate(zip(filas_f, filas_v), start=fila_enc + 1):
        fila_val, fila_esf, fila_frm = {}, {}, {}
        for k in claves:
            i = indices[k] - 1
            cf = rf[i] if i < len(rf) else None
            cv = rv[i] if i < len(rv) else None
            es_f = cf is not None and getattr(cf, "data_type", None) == "f"
            fila_esf[k] = es_f
            fila_frm[k] = cf.value if es_f else None
            fila_val[k] = cv.value if cv is not None else None
        filas.append(n)
        vals.append(fila_val)
        esf.append(fila_esf)
        frm.append(fila_frm)

    valores = pd.DataFrame(vals, index=filas, columns=claves, dtype=object)
    es_formula = pd.DataFrame(esf, index=filas, columns=claves, dtype=bool)
    formulas = pd.DataFrame(frm, index=filas, columns=claves, dtype=object)
    for df in (valores, es_formula, formulas):
        df.index.name = "fila"

    columna_clave = "nombre_comun" if hoja == HOJA_BD else "id"
    filas_datos = [f for f in filas if not es_vacio(valores.at[f, columna_clave])]
    usuario = [c for c in COLUMNAS_USUARIO.get(hoja, []) if c in columnas]
    filas_sin_id = [
        f
        for f in filas
        if es_vacio(valores.at[f, columna_clave])
        and any(not es_vacio(valores.at[f, c]) and not es_formula.at[f, c] for c in usuario)
    ]
    return Tabla(hoja, fila_enc, columnas, encabezados, valores, es_formula, formulas, filas_datos, filas_sin_id)


def _valores_rango(ws, c1: str, f1: int, c2: str, f2: int) -> list[tuple[str, str]]:
    """(valor, celda) de los valores no vacíos del rango."""
    out = []
    for fila in ws.iter_rows(
        min_row=f1, max_row=f2, min_col=column_index_from_string(c1),
        max_col=column_index_from_string(c2),
    ):
        for celda in fila:
            if not es_vacio(celda.value):
                out.append((limpiar(celda.value), celda.coordinate))
    return out


def _leer_listas(z: zipfile.ZipFile, partes: dict, wb_v, tablas: list[Tabla], avisos: list[str]):
    listas: dict[str, list[str]] = {}
    origen: dict[str, str] = {}
    refs: dict[str, list[tuple[str, str]]] = {}
    for t in tablas:
        if t.hoja not in partes:
            continue
        xml = z.read(partes[t.hoja]).decode("utf-8")
        for sqref, formula in xlsx_xml.validaciones_lista(xml):
            m = _RANGO_RE.match(formula)
            if not m or m.group(1) not in wb_v.sheetnames:
                continue
            for letra in xlsx_xml.columnas_de_sqref(sqref):
                clave = t.clave_de_letra(letra)
                if clave in _LISTAS_POR_ENCABEZADO and clave not in listas:
                    pares = _valores_rango(
                        wb_v[m.group(1)], m.group(2), int(m.group(3)), m.group(4), int(m.group(5))
                    )
                    listas[clave] = [v for v, _ in pares]
                    refs[clave] = [(m.group(1), r) for _, r in pares]
                    origen[clave] = f"lista desplegable de {t.hoja}!{letra} → {formula}"

    if HOJA_VALIDACION in wb_v.sheetnames:
        ws = wb_v[HOJA_VALIDACION]
        filas = [list(r) for r in ws.iter_rows(values_only=True)]
        if filas:
            for clave, alias in _LISTAS_POR_ENCABEZADO.items():
                if clave in listas:
                    continue
                for j, enc in enumerate(filas[0]):
                    if _coincide(enc, alias):
                        vals, celdas = [], []
                        for i, fila in enumerate(filas[1:], start=2):
                            v = fila[j] if j < len(fila) else None
                            if es_vacio(v):
                                break
                            vals.append(limpiar(v))
                            celdas.append((HOJA_VALIDACION, f"{get_column_letter(j + 1)}{i}"))
                        listas[clave] = vals
                        refs[clave] = celdas
                        origen[clave] = (
                            f"'{HOJA_VALIDACION}'!{get_column_letter(j + 1)} (hasta la primera celda vacía)"
                        )
                        break
    for clave in _LISTAS_POR_ENCABEZADO:
        if clave not in listas:
            avisos.append(f"No se encontró la lista de valores permitidos para '{clave}'.")
    return listas, origen, refs


def _leer_directo_indirecto(registros: list[Tabla], config: dict):
    """Lee los tipos Directo/Indirecto desde la fórmula de la propia planilla."""
    for t in registros:
        if "dir_ind" not in t.columnas:
            continue
        serie = t.formulas["dir_ind"].dropna()
        if serie.empty:
            continue
        f = str(serie.iloc[0])
        i_d, i_i = f.find('"Directo"'), f.find('"Indirecto"')
        if 0 < i_d < i_i:
            patron = r'[A-Z]{1,3}\$?\d+\s*=\s*"([^"]*)"'
            directo = re.findall(patron, f[:i_d])
            indirecto = re.findall(patron, f[i_d:i_i])
            if directo and indirecto:
                return (
                    {"Directo": directo, "Indirecto": indirecto},
                    f"fórmula de {t.hoja}!{t.columnas['dir_ind']}{serie.index[0]}",
                )
    return dict(config["directo_indirecto"]), "config (no se pudo leer la fórmula)"


def leer_libro(ruta_xlsx: str | Path, config: dict, formato: str = "xlsx", ruta_original=None) -> Libro:
    ruta_xlsx = Path(ruta_xlsx)
    avisos: list[str] = []
    estructura = config["estructura"]
    # openpyxl avisa que descarta las validaciones x14: aquí solo se lee, no se guarda.
    warnings.filterwarnings("ignore", message="Data Validation extension", module="openpyxl")
    wb_f = openpyxl.load_workbook(ruta_xlsx, read_only=True, data_only=False)
    wb_v = openpyxl.load_workbook(ruta_xlsx, read_only=True, data_only=True)
    try:
        estaciones = _leer_tabla(wb_f, wb_v, HOJA_ESTACIONES, estructura[HOJA_ESTACIONES], avisos)
        nombres_bd = _leer_tabla(wb_f, wb_v, HOJA_BD, estructura[HOJA_BD], avisos)
        registros, sin_datos = [], []
        for hoja in (HOJA_REGISTROS, HOJA_TA):
            if hoja not in wb_v.sheetnames:
                if hoja == HOJA_REGISTROS:
                    raise ErrorEstructura("Falta la hoja 'Registros'")
                continue
            t = _leer_tabla(wb_f, wb_v, hoja, estructura[hoja], avisos)
            if t.filas_datos or t.filas_sin_id or hoja == HOJA_REGISTROS:
                registros.append(t)
            else:
                sin_datos.append(hoja)
        with zipfile.ZipFile(ruta_xlsx) as z:
            partes = xlsx_xml.mapa_hojas(z)
            listas, origen, refs = _leer_listas(z, partes, wb_v, [estaciones, *registros], avisos)
    finally:
        wb_f.close()
        wb_v.close()
    di, origen_di = _leer_directo_indirecto(registros, config)
    return Libro(
        ruta=ruta_xlsx,
        ruta_original=Path(ruta_original or ruta_xlsx),
        formato=formato,
        partes=partes,
        estaciones=estaciones,
        registros=registros,
        registros_sin_datos=sin_datos,
        nombres_bd=nombres_bd,
        listas=listas,
        origen_listas=origen,
        listas_refs=refs,
        directo_indirecto=di,
        origen_directo_indirecto=origen_di,
        avisos=avisos,
    )


def tabla_mapeo(libro: Libro) -> list[dict]:
    """Filas para mostrar el mapeo de columnas detectado."""
    out = []
    for t in libro.tablas():
        for clave, letra in t.columnas.items():
            out.append(
                {"hoja": t.hoja, "fila_encabezado": t.fila_encabezado, "clave": clave,
                 "columna": letra, "encabezado": t.encabezados[clave],
                 "formula": clave in t.columnas_formula() or clave in t.columnas_naranjas()}
            )
    return out
