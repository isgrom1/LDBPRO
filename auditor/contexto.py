"""Datos derivados que comparten las reglas: uniones rehechas en código, coordenadas limpias.

Se distinguen tres formas de unir, porque se comportan distinto:
  * exacta   : como un join de R/pandas (distingue mayúsculas y espacios). Es la que vale.
  * flexible : ignorando mayúsculas y espacios; sirve para diagnosticar y sugerir.
  * Excel    : MATCH(...;0) de Excel, que ignora mayúsculas pero no espacios; solo se usa
               para verificar los valores cacheados de las fórmulas.
"""

from __future__ import annotations

import math
from collections import defaultdict

import pandas as pd

from .hallazgos import REGLAS, Correccion, Hallazgo
from .modelo import HOJA_BD, HOJA_TA, Libro, Tabla
from .texto import a_fecha, a_hora, a_numero, clave, limpiar, mostrar

_NADA = object()
COORDS = ["utm_e", "utm_s", "utm_e_ini", "utm_s_ini", "utm_e_fin", "utm_s_fin"]
METODOLOGIA_TA = "Tránsito aéreo"


def texto_id(v) -> str:
    """Texto con que un join compararía el valor (los números como se ven en Excel)."""
    if v is None:
        return ""
    return v if isinstance(v, str) else mostrar(v)


def distancia(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


class Contexto:
    def __init__(self, libro: Libro, config: dict):
        self.libro = libro
        self.config = config
        self.est: Tabla = libro.estaciones
        self.bd: Tabla = libro.nombres_bd
        self.alias_clase = {clave(k): v for k, v in config.get("clases_alias", {}).items()}
        # Cifras que calculan las reglas y que el reporte muestra aunque no haya hallazgo (p. ej. R11)
        self.estadisticas: dict[str, dict] = {}
        self._preparar_estaciones()
        self._preparar_bd()
        self.uniones: dict[str, pd.DataFrame] = {t.hoja: self._unir(t) for t in libro.registros}

    # ------------------------------------------------------------------ estaciones
    def _preparar_estaciones(self):
        self.est_por_id: dict[str, int] = {}
        self.est_por_clave: dict[str, list[int]] = defaultdict(list)
        self.est_por_excel: dict[str, int] = {}
        filas = []
        for f in self.est.filas_datos:
            idt = texto_id(self.est.valor(f, "id"))
            self.est_por_id.setdefault(idt, f)
            self.est_por_clave[clave(idt)].append(f)
            self.est_por_excel.setdefault(idt.casefold(), f)
            filas.append({c: a_numero(self.est.valor(f, c)) for c in COORDS if self.est.tiene(c)})
        self.coords = pd.DataFrame(filas, index=self.est.filas_datos, columns=COORDS, dtype=float)

    def id_de_estacion(self, fila: int) -> str:
        return limpiar(self.est.valor(fila, "id"))

    def punto(self, fila: int, cual: str = "") -> tuple[float, float] | None:
        """Punto de la estación ('' = estación, '_ini', '_fin') o None si falta."""
        e, s = self.coords.at[fila, f"utm_e{cual}"], self.coords.at[fila, f"utm_s{cual}"]
        if pd.isna(e) or pd.isna(s) or e == 0 or s == 0:
            return None
        return (float(e), float(s))

    def punto_medio(self, fila: int) -> tuple[float, float] | None:
        a, b = self.punto(fila, "_ini"), self.punto(fila, "_fin")
        if a is None or b is None:
            return None
        return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)

    def largo_transecto(self, fila: int) -> float | None:
        a, b = self.punto(fila, "_ini"), self.punto(fila, "_fin")
        return None if a is None or b is None else distancia(a, b)

    def punto_referencia(self, fila: int) -> tuple[float, float] | None:
        return self.punto(fila) or self.punto_medio(fila)

    # ------------------------------------------------------------------ nombres BD
    def _preparar_bd(self):
        self.bd_exacto: dict[str, int] = {}
        self.bd_clave: dict[str, list[int]] = defaultdict(list)
        self.bd_excel: dict[str, int] = {}
        self.bd_por_genero: dict[str, list[int]] = defaultdict(list)
        for f in self.bd.filas_datos:
            nombre = texto_id(self.bd.valor(f, "nombre_comun"))
            self.bd_exacto.setdefault(nombre, f)
            self.bd_clave[clave(nombre)].append(f)
            self.bd_excel.setdefault(nombre.casefold(), f)
            genero = limpiar(self.bd.valor(f, "genero")) or limpiar(self.bd.valor(f, "nombre_cientifico")).split(" ")[0]
            if genero and genero != "-":
                self.bd_por_genero[clave(genero)].append(f)

    def cientifico_bd(self, fila_bd: int) -> str:
        return limpiar(self.bd.valor(fila_bd, "nombre_cientifico"))

    def clase_normalizada(self, valor) -> str:
        c = limpiar(valor)
        return self.alias_clase.get(clave(c), c)

    def clase_bd(self, fila_bd: int) -> str:
        return self.clase_normalizada(self.bd.valor(fila_bd, "clase"))

    def resolver_nombre(self, nombre) -> tuple[int | None, int | None, list[int]]:
        """(fila exacta, fila flexible única, candidatos flexibles) en nombres BD."""
        nt = texto_id(nombre)
        exacta = self.bd_exacto.get(nt)
        candidatos = self.bd_clave.get(clave(nt), [])
        flexible = exacta
        if flexible is None and candidatos:
            especies = {clave(self.cientifico_bd(c)) for c in candidatos}
            if len(especies) == 1:
                flexible = candidatos[0]
        return exacta, flexible, candidatos

    # ------------------------------------------------------------------ registros
    def _clasificar_tipo(self, tipo) -> str:
        """Lo mismo que la fórmula Directo/Indirecto (las comparaciones de Excel ignoran mayúsculas)."""
        t = texto_id(tipo).casefold()
        for categoria in ("Directo", "Indirecto"):
            if t in {x.casefold() for x in self.libro.directo_indirecto.get(categoria, [])}:
                return categoria
        return "REVISAR"

    def _unir(self, t: Tabla) -> pd.DataFrame:
        filas = []
        for f in t.filas_datos:
            idt = texto_id(t.valor(f, "id"))
            est_exacta = self.est_por_id.get(idt)
            cand_est = self.est_por_clave.get(clave(idt), [])
            nombre = t.valor(f, "nombre_comun")
            bd_exacta, bd_flex, cand_bd = self.resolver_nombre(nombre)
            metodologia = limpiar(t.valor(f, "metodologia")) if t.tiene("metodologia") else ""
            if t.hoja == HOJA_TA and not metodologia:
                metodologia = METODOLOGIA_TA
            tipo = t.valor(f, "tipo_registro")
            filas.append(
                {
                    "id": limpiar(idt),
                    "est_fila": est_exacta,
                    "est_fila_flex": est_exacta if est_exacta is not None else (cand_est[0] if cand_est else None),
                    "bd_fila": bd_exacta,
                    "bd_fila_flex": bd_flex,
                    "n_candidatos_bd": len(cand_bd),
                    "nombre": limpiar(nombre),
                    "nombre_bd": texto_id(self.bd.valor(bd_flex, "nombre_comun")) if bd_flex else None,
                    "especie": self.cientifico_bd(bd_flex) if bd_flex else None,
                    "clase": self.clase_bd(bd_flex) if bd_flex else None,
                    "fecha": a_fecha(t.valor(f, "fecha")),
                    "hora": a_hora(t.valor(f, "hora")),
                    "abundancia": a_numero(t.valor(f, "abundancia")),
                    "metodologia": metodologia,
                    "tipo_registro": limpiar(tipo),
                    "dir_ind_calc": self._clasificar_tipo(tipo) if t.tiene("tipo_registro") else None,
                }
            )
        df = pd.DataFrame(filas, index=t.filas_datos, dtype=object)
        df.index.name = "fila"
        return df

    def iter_registros(self):
        """(tabla, fila, unión) para cada registro de cada hoja de registros con datos."""
        for t in self.libro.registros:
            u = self.uniones[t.hoja]
            for f in t.filas_datos:
                yield t, f, u.loc[f]

    # ------------------------------------------------------------------ hallazgos
    def hallazgo(
        self, id_regla: str, tabla: Tabla | str, fila: int | None, clave_col: str | None,
        problema: str, sugerencia: str, valor=_NADA, correccion=_NADA, celda: str | None = None,
        id_estacion: str | None = None, celdas_extra=(),
    ) -> Hallazgo:
        r = REGLAS[id_regla]
        if isinstance(tabla, Tabla):
            hoja = tabla.hoja
            if celda is None and fila is not None and clave_col is not None:
                celda = tabla.celda(fila, clave_col)
            if valor is _NADA:
                valor = tabla.valor(fila, clave_col) if fila is not None and clave_col else None
            if id_estacion is None and fila is not None and tabla.hoja != HOJA_BD and tabla.tiene("id"):
                id_estacion = limpiar(tabla.valor(fila, "id")) or None
        else:
            hoja = tabla
            valor = None if valor is _NADA else valor
        corr = None
        if correccion is not _NADA:
            if not r.auto_corregible:
                raise ValueError(f"{id_regla} no está registrada como auto-corregible")
            corr = Correccion(hoja, celda, fila, clave_col, correccion)
        return Hallazgo(
            id_regla=id_regla, hoja=hoja, celda=celda, fila=fila, id_estacion=id_estacion,
            valor_actual=valor, problema=problema, sugerencia=sugerencia, severidad=r.severidad,
            correccion=corr, celdas_extra=list(celdas_extra),
        )

