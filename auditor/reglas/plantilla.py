"""Reglas sobre el diccionario, la plantilla y los valores cacheados de las fórmulas."""

from __future__ import annotations

import difflib
import re

from ..contexto import texto_id
from ..hallazgos import Severidad, regla
from ..modelo import HOJA_BD, HOJA_ESTACIONES, Libro
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


@regla("B01", "Lista de especies de la plantilla (nombres BD) con entradas problemáticas", INFO)
def b01(ctx):
    """La hoja «nombres BD» viene en la plantilla de XPE: solo se avisa, no se corrige."""
    bd = ctx.bd
    for f in bd.filas_datos:
        nombre = bd.valor(f, "nombre_comun")
        espacios = describir_espacios(nombre)
        if espacios:
            yield ctx.hallazgo(
                "B01", bd, f, "nombre_comun",
                f"«{limpiar(nombre)}» tiene {', '.join(espacios)} en nombres BD: si en Registros se escribe "
                "sin ese espacio, la planilla da #N/A.",
                "No es un error de tu planilla: el auditor une los nombres ignorando esos espacios.",
            )
        if bd.tiene("clase"):
            c = limpiar(bd.valor(f, "clase"))
            normal = ctx.clase_normalizada(c)
            if c and normal != c:
                yield ctx.hallazgo(
                    "B01", bd, f, "clase",
                    f"La clase de «{limpiar(nombre)}» está como «{c}» en vez de «{normal}» en nombres BD.",
                    f"No es un error de tu planilla: el auditor la cuenta como {normal} al agrupar por clase.",
                )
    for filas in ctx.bd_clave.values():
        especies = {clave(ctx.cientifico_bd(f)) for f in filas}
        if len(filas) > 1 and len(especies) > 1:
            nombre = limpiar(bd.valor(filas[0], "nombre_comun"))
            detalle = "; ".join(f"fila {f}: {ctx.cientifico_bd(f) or '(sin nombre científico)'}" for f in filas)
            yield ctx.hallazgo(
                "B01", bd, filas[1], "nombre_comun",
                f"«{nombre}» aparece {len(filas)} veces en nombres BD con especies distintas ({detalle}). Al "
                f"escribir «{nombre}» en Registros, la planilla siempre completa la primera "
                f"({ctx.cientifico_bd(filas[0])}).",
                "No es un error de tu planilla. Solo importa si registraste otra de esas especies (lo marca R27).",
                celdas_extra=[bd.celda(f, "nombre_comun") for f in filas if f != filas[1]],
            )


@regla("R27", "Nombre común con varias especies en nombres BD", INFO)
def r27(ctx):
    for t, f, u in ctx.iter_registros():
        filas = ctx.bd_clave.get(clave(u["nombre"]), [])
        especies = {clave(ctx.cientifico_bd(g)) for g in filas}
        if len(filas) < 2 or len(especies) < 2 or not u["especie"]:
            continue
        otras = sorted({ctx.cientifico_bd(g) for g in filas} - {u["especie"]})
        yield ctx.hallazgo(
            "R27", t, f, "nombre_comun",
            f"«{u['nombre']}» corresponde a {len(especies)} especies en nombres BD; la planilla completó "
            f"{u['especie']} (la primera). Las otras: {', '.join(otras)}.",
            "Confirma que sea esa especie. Si era otra, la plantilla no permite elegirla: avísale a XPE.",
            justificaciones=[f"Es {u['especie']}"],
        )


@regla("T01", "Plantilla: una fórmula y su lista de validación no coinciden", INFO, auto_corregible=True)
def t01(ctx):
    """Se corrige la lista de 'validación datos' (una celda) para que coincida con la fórmula,
    en vez de reescribir las fórmulas de cientos de filas."""
    yield from _t01_directo_indirecto(ctx)
    yield from _t01_comportamiento(ctx)


def _fila(celda: str) -> int:
    return int(re.sub(r"[A-Z]", "", celda))


def _t01_comportamiento(ctx):
    """La fórmula de ayuda de Comportamiento escribe valores que no están en la lista."""
    lista = ctx.libro.listas.get("comportamiento") or []
    refs = ctx.libro.listas_refs.get("comportamiento") or []
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
            problema = (
                f"La fórmula de ayuda de Comportamiento ({formula}) escribe '{v}', que no está en la lista "
                f"de 'validación datos'{f' (la lista dice {opciones[parecido[0]]!r})' if parecido else ''}. "
                f"Afecta a {len(filas)} registros."
            )
            if parecido and len(refs) == len(lista):
                hoja, celda = refs[lista.index(opciones[parecido[0]])]
                yield ctx.hallazgo(
                    "T01", hoja, _fila(celda), f"{Libro.PREFIJO_LISTA}comportamiento", problema,
                    f"Se cambia «{opciones[parecido[0]]}» por «{v}» en la lista, igual que la fórmula.",
                    celda=celda, valor=opciones[parecido[0]], correccion=v,
                )
            else:
                yield ctx.hallazgo(
                    "T01", t, None, None, problema,
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
        problema = (
            f"La lista de tipos de registro ofrece '{valor}', pero la fórmula Directo/Indirecto de "
            f"Registros no lo reconoce{f' (espera {pista!r})' if pista else ''}: quien lo elija obtiene 'REVISAR'."
        )
        if pista and celda:
            yield ctx.hallazgo(
                "T01", hoja, _fila(celda), f"{Libro.PREFIJO_LISTA}tipo_registro", problema,
                f"Se cambia «{valor}» por «{pista}» en la lista, igual que la fórmula.",
                celda=celda, valor=valor, correccion=pista,
            )
        else:
            yield ctx.hallazgo(
                "T01", hoja or "validación datos", None, None, problema,
                f"Agrega '{valor}' a la fórmula de Directo/Indirecto.", celda=celda, valor=valor,
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
