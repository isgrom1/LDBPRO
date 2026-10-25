"""Reglas sobre el diccionario, la plantilla y los valores cacheados de las fórmulas."""

from __future__ import annotations

import difflib
import re

from ..contexto import texto_id
from ..hallazgos import Accion, Severidad, regla
from ..modelo import HOJA_BD, HOJA_ESTACIONES
from ..texto import (
    a_numero,
    clave,
    describir_espacios,
    es_numero_real,
    es_vacio,
    limpiar,
    mostrar,
)

ADV, INFO = Severidad.ADVERTENCIA, Severidad.INFO

_INDEX_MATCH = re.compile(
    r"^=?INDEX\(\s*'?([^'!]+)'?!\$?([A-Z]{1,3})\$?\d*:\$?[A-Z]{1,3}\$?\d*\s*,\s*MATCH\(", re.I
)


@regla("B01", "Diccionario nombres BD con entradas problemáticas", INFO)
def b01(ctx):
    bd = ctx.bd
    for f in bd.filas_datos:
        nombre = bd.valor(f, "nombre_comun")
        espacios = describir_espacios(nombre)
        if espacios:
            yield ctx.hallazgo(
                "B01", bd, f, "nombre_comun",
                f"Nombre común con {', '.join(espacios)}: quien lo escriba sin ese espacio obtiene #N/A en Excel.",
                f"Corrige el nombre a '{limpiar(nombre)}' en nombres BD.",
                acciones=[Accion(f"Usar «{limpiar(nombre)}»", limpiar(nombre))],
            )
        if bd.tiene("clase"):
            c = limpiar(bd.valor(f, "clase"))
            normal = ctx.clase_normalizada(c)
            if c and normal != c:
                yield ctx.hallazgo(
                    "B01", bd, f, "clase",
                    f"Clase '{c}' en vez de '{normal}': al agrupar por clase quedan dos grupos.",
                    f"Usa '{normal}' (como el resto del diccionario).",
                    acciones=[Accion(f"Usar «{normal}»", normal)],
                )
    for filas in ctx.bd_clave.values():
        especies = {clave(ctx.cientifico_bd(f)) for f in filas}
        if len(filas) > 1 and len(especies) > 1:
            for f in filas[1:]:
                yield ctx.hallazgo(
                    "B01", bd, f, "nombre_comun",
                    f"El nombre común '{limpiar(bd.valor(f, 'nombre_comun'))}' se repite en las filas "
                    f"{', '.join(map(str, filas))} con especies distintas; el MATCH de Excel toma siempre la primera ({filas[0]}).",
                    "Deja un nombre común único por especie.",
                )


@regla("T01", "Plantilla: una fórmula y su lista de validación no coinciden", INFO)
def t01(ctx):
    yield from _t01_directo_indirecto(ctx)
    yield from _t01_comportamiento(ctx)


def _t01_comportamiento(ctx):
    """La fórmula de ayuda de Comportamiento escribe valores que no están en la lista."""
    lista = ctx.libro.listas.get("comportamiento") or []
    opciones = {clave(x): x for x in lista}
    for t in ctx.libro.registros:
        if not t.tiene("comportamiento") or not lista:
            continue
        en_formula = [f for f in t.filas_datos if t.es_formula.at[f, "comportamiento"]]
        por_valor = {}
        for f in en_formula:
            v = limpiar(t.valor(f, "comportamiento"))
            if v and clave(v) != "completar" and v not in lista:
                por_valor.setdefault(v, []).append(f)
        for v, filas in por_valor.items():
            parecido = difflib.get_close_matches(clave(v), list(opciones), n=1, cutoff=0.6)
            formula = t.formulas.at[filas[0], "comportamiento"]
            yield ctx.hallazgo(
                "T01", t, None, None,
                f"La fórmula de ayuda de Comportamiento ({formula}) escribe '{v}', que no está en la lista "
                f"de 'validación datos'{f' (la lista dice {opciones[parecido[0]]!r})' if parecido else ''}. "
                f"Afecta a {len(filas)} registros.",
                "Corrige la fórmula de la plantilla o la lista para que usen el mismo término.",
                celda=f"{t.columnas['comportamiento']}{t.fila_encabezado}", valor=v,
            )


def _t01_directo_indirecto(ctx):
    di = ctx.libro.directo_indirecto
    reconocidos = {clave(x) for x in di.get("Directo", []) + di.get("Indirecto", [])}
    lista = ctx.libro.listas.get("tipo_registro") or []
    refs = ctx.libro.listas_refs.get("tipo_registro") or [(None, None)] * len(lista)
    for valor, (hoja, celda) in zip(lista, refs):
        if clave(valor) in reconocidos or clave(valor) == "otro":
            continue
        pista = next(
            (x for x in di.get("Directo", []) + di.get("Indirecto", []) if clave(x) in (clave(valor) + "s", clave(valor)[:-1])),
            None,
        )
        yield ctx.hallazgo(
            "T01", hoja or "validación datos", None, None,
            f"La lista de tipos de registro ofrece '{valor}', pero la fórmula Directo/Indirecto de "
            f"Registros no lo reconoce{f' (espera {pista!r})' if pista else ''}: quien lo elija obtiene 'REVISAR'.",
            f"Corrige la plantilla: usa '{pista}' en la lista o agrega '{valor}' a la fórmula."
            if pista else f"Agrega '{valor}' a la fórmula de Directo/Indirecto.",
            celda=celda, valor=valor,
        )


@regla("X01", "Columna de fórmula con un valor fijo", ADV)
def x01(ctx):
    for t in ctx.libro.registros:
        for c in sorted(t.columnas_naranjas()):
            for f in t.filas_datos:
                if not t.es_formula.at[f, c] and not es_vacio(t.valor(f, c)):
                    yield ctx.hallazgo(
                        "X01", t, f, c,
                        f"La columna naranja '{t.encabezados[c]}' tiene un valor fijo en vez de la fórmula: "
                        "no se actualiza si cambia la estación o la especie.",
                        "Restaura la fórmula copiándola de la fila de arriba.",
                    )


def _comparable(v):
    if v is None or (isinstance(v, str) and v == ""):
        return ""
    if es_numero_real(v):
        return float(v)
    return v if isinstance(v, str) else mostrar(v)


@regla("X02", "Valor cacheado de una fórmula desactualizado", INFO, usa_cache=True)
def x02(ctx):
    """Rehace cada INDEX/MATCH como lo haría Excel y lo compara con el valor guardado."""
    destinos = {HOJA_ESTACIONES: (ctx.est, ctx.est_por_excel, "id"), HOJA_BD: (ctx.bd, ctx.bd_excel, "nombre_comun")}
    for t in ctx.libro.registros:
        for c in sorted(t.columnas_naranjas()):
            for f in t.filas_datos:
                formula = t.formulas.at[f, c]
                m = _INDEX_MATCH.match(str(formula or ""))
                if not m or m.group(1) not in destinos:
                    continue
                tabla, indice, buscar = destinos[m.group(1)]
                k = tabla.clave_de_letra(m.group(2))
                if k is None:
                    continue
                g = indice.get(texto_id(t.valor(f, buscar)).casefold())
                if g is None:
                    esperado = "#N/A"
                else:
                    esperado = tabla.valor(g, k)
                    esperado = 0 if es_vacio(esperado) and not isinstance(esperado, str) else esperado
                cache = t.valor(f, c)
                if cache is None:
                    continue  # archivo guardado sin valores calculados (p. ej. por otro programa)
                if _comparable(cache) == _comparable(esperado):
                    continue
                if es_numero_real(cache) and a_numero(esperado) == cache:
                    continue
                yield ctx.hallazgo(
                    "X02", t, f, c,
                    f"El valor guardado de la fórmula ('{mostrar(cache)}') no coincide con lo que da al "
                    f"recalcular ('{mostrar(esperado)}'): el archivo se editó fuera de Excel o sin recalcular.",
                    "Ábrelo en Excel y recalcula (Ctrl+Alt+F9) antes de exportar. Este auditor ya rehace "
                    "las uniones en código y no usa ese valor.",
                )
