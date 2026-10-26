"""Reglas sobre la hoja Estaciones."""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from difflib import SequenceMatcher

from ..contexto import COORDS, distancia, texto_id
from ..hallazgos import Accion, Severidad, regla
from ..texto import (
    a_numero,
    clave,
    describir_espacios,
    es_numero_real,
    es_vacio,
    limpiar,
    mostrar,
    numero_para_celda,
    tokens,
)

CRIT, ADV, INFO = Severidad.CRITICO, Severidad.ADVERTENCIA, Severidad.INFO

NOMBRE_COORD = {
    "utm_e": "UTM E de la estación", "utm_s": "UTM S de la estación",
    "utm_e_ini": "UTM E de inicio", "utm_s_ini": "UTM S de inicio",
    "utm_e_fin": "UTM E de fin", "utm_s_fin": "UTM S de fin",
}
NOMBRE_PUNTO = {"": "el punto de la estación", "_ini": "el inicio del transecto", "_fin": "el fin del transecto"}
NOMBRE_PUNTO_AL = {"": "al punto de la estación", "_ini": "al inicio del transecto", "_fin": "al fin del transecto"}


def _xy(p) -> str:
    return f"({p[0]:.0f}, {p[1]:.0f})"


@regla("E01", "ID de estación duplicado", CRIT)
def e01(ctx):
    est = ctx.est
    for filas in ctx.est_por_clave.values():
        if len(filas) < 2:
            continue
        variantes = {texto_id(est.valor(f, "id")) for f in filas}
        for f in filas:
            otras = [x for x in filas if x != f]
            extra = " (difieren solo en mayúsculas o espacios)" if len(variantes) > 1 else ""
            yield ctx.hallazgo(
                "E01", est, f, "id",
                f"El ID '{limpiar(est.valor(f, 'id'))}' también está en la(s) fila(s) "
                f"{', '.join(map(str, otras))}{extra}.",
                f"Cada estación debe tener un ID único. Excel une los registros solo con la primera "
                f"fila ({filas[0]}) y un join en R los duplica. Renombra o elimina la fila repetida.",
                celdas_extra=[est.celda(x, "id") for x in otras],
            )


@regla("E02", "Ambiente vacío", CRIT)
def e02(ctx):
    for f in ctx.est.filas_datos:
        if es_vacio(ctx.est.valor(f, "ambiente")):
            yield ctx.hallazgo(
                "E02", ctx.est, f, "ambiente",
                "Ambiente vacío: la estación y sus registros quedan fuera de cualquier análisis por "
                "ambiente (en Registros la fórmula muestra 0).",
                "Completa el ambiente (formación vegetacional) de la estación.",
            )


def _grupos_similares(valores: list[str]) -> list[set[str]]:
    """Agrupa variantes del mismo texto.

    Son variantes si: (a) difieren solo en mayúsculas, espacios, puntuación o tildes;
    (b) tienen las mismas palabras salvo un error de tipeo; o (c) a una le falta la palabra
    cabecera que comparten las demás ('Medio alto' vs 'Matorral medio-alto').
    No se agrupan categorías distintas como 'Matorral bajo' y 'Matorral medio-bajo'.
    """
    distintos = sorted(set(valores))
    toks = {v: tokens(v) for v in distintos}
    primeras = Counter(toks[v][0] for v in distintos if toks[v])
    cabeceras = {t for t, n in primeras.items() if n >= 2}

    def variantes(a: str, b: str) -> bool:
        ta, tb = toks[a], toks[b]
        if ta == tb:
            return True
        ja, jb = " ".join(ta), " ".join(tb)
        if len(ta) == len(tb) and min(len(ja), len(jb)) >= 5 and SequenceMatcher(None, ja, jb).ratio() >= 0.9:
            return True
        corto, largo = (ta, tb) if len(ta) < len(tb) else (tb, ta)
        if corto and largo[len(largo) - len(corto):] == corto:
            return all(t in cabeceras for t in largo[: len(largo) - len(corto)])
        return False

    padre = {v: v for v in distintos}

    def raiz(v):
        while padre[v] != v:
            v = padre[v]
        return v

    for i, a in enumerate(distintos):
        for b in distintos[i + 1 :]:
            if variantes(a, b):
                padre[raiz(b)] = raiz(a)
    grupos = defaultdict(set)
    for v in distintos:
        grupos[raiz(v)].add(v)
    return [g for g in grupos.values() if len(g) > 1]


@regla("E03", "Variantes del mismo ambiente", ADV)
def e03(ctx):
    est = ctx.est
    por_valor = defaultdict(list)
    for f in est.filas_datos:
        v = est.valor(f, "ambiente")
        if not es_vacio(v):
            por_valor[texto_id(v)].append(f)
    for grupo in _grupos_similares(list(por_valor)):
        # Forma sugerida: la más frecuente; si empatan, la más completa.
        canonica = max(grupo, key=lambda v: (len(por_valor[v]), len(limpiar(v)), v))
        resumen = ", ".join(f"'{mostrar(v)}' ({len(por_valor[v])})" for v in sorted(grupo))
        for v in grupo:
            if v == canonica:
                continue
            for f in por_valor[v]:
                yield ctx.hallazgo(
                    "E03", est, f, "ambiente",
                    f"'{mostrar(v)}' parece una variante de '{canonica}'. Variantes encontradas: {resumen}. "
                    "R y PAST las tratarán como ambientes distintos.",
                    f"Unifica el nombre del ambiente (sugerido: '{canonica}').",
                    acciones=[Accion(f"Usar «{canonica}»", canonica)],
                )


@regla("E04", "ID Final Estación vacío o distinto del ID", ADV, auto_corregible=True)
def e04(ctx):
    est = ctx.est
    if not est.tiene("id_final"):
        return
    for f in est.filas_datos:
        id_ = est.valor(f, "id")
        final = est.valor(f, "id_final")
        if texto_id(final) == texto_id(id_):
            continue
        if es_vacio(final):
            yield ctx.hallazgo(
                "E04", est, f, "id_final", "ID Final Estación vacío.",
                f"Se copia el ID de la estación ('{texto_id(id_)}').", correccion=texto_id(id_),
            )
        elif clave(final) == clave(id_):
            yield ctx.hallazgo(
                "E04", est, f, "id_final",
                f"ID Final '{mostrar(final)}' difiere del ID '{texto_id(id_)}' solo en mayúsculas/espacios.",
                "Se copia el ID de la estación.", correccion=texto_id(id_),
            )
        else:
            yield ctx.hallazgo(
                "E04", est, f, "id_final",
                f"ID Final '{mostrar(final)}' es distinto del ID '{texto_id(id_)}'.",
                "Si el cambio de nombre es intencional (código final del informe), déjalo; si no, "
                "copia el ID. No se corrige solo para no borrar un renombre deliberado.",
                acciones=[Accion(f"Copiar el ID «{texto_id(id_)}»", texto_id(id_))],
            )


@regla("E05", "Coordenada no numérica", CRIT, auto_corregible=True)
def e05(ctx):
    est = ctx.est
    for f in est.filas_datos:
        for c in COORDS:
            v = est.valor(f, c)
            if es_vacio(v) or es_numero_real(v):
                continue
            n = a_numero(v)
            detalle = describir_espacios(v)
            motivo = f" ({', '.join(detalle)})" if detalle else ""
            if n is not None:
                yield ctx.hallazgo(
                    "E05", est, f, c,
                    f"{NOMBRE_COORD[c]} está guardada como texto{motivo}: Excel la muestra bien, pero "
                    "R/PAST la leen como NA o como texto y las distancias fallan.",
                    f"Se convierte al número {numero_para_celda(n)}.", correccion=numero_para_celda(n),
                )
            else:
                yield ctx.hallazgo(
                    "E05", est, f, c,
                    f"{NOMBRE_COORD[c]} no es un número: '{mostrar(v)}'.",
                    "Escribe la coordenada UTM como número, sin letras ni separadores.",
                )


@regla("E06", "Coordenada vacía o 0", CRIT)
def e06(ctx):
    est = ctx.est
    capturas = defaultdict(list)
    for t, f, u in ctx.iter_registros():
        if u["est_fila_flex"] is not None and t.tiene("utm_e_captura"):
            e, s = a_numero(t.valor(f, "utm_e_captura")), a_numero(t.valor(f, "utm_s_captura"))
            if e and s:
                capturas[u["est_fila_flex"]].append((e, s))
    for f in est.filas_datos:
        for c in COORDS:
            v = est.valor(f, c)
            if not (es_vacio(v) or a_numero(v) == 0):
                continue
            sug = "Completa la coordenada desde el GPS o el track de terreno."
            if capturas.get(f):
                pts = capturas[f]
                med = (statistics.median(p[0] for p in pts), statistics.median(p[1] for p in pts))
                sug += (
                    f" Referencia: la captura registrada en esta estación está en {_xy(med)}."
                    if len(pts) == 1 else
                    f" Referencia: la mediana de las {len(pts)} capturas de esta estación es {_xy(med)}."
                )
            yield ctx.hallazgo(
                "E06", est, f, c, f"{NOMBRE_COORD[c]} {'vacía' if es_vacio(v) else 'es 0'}.", sug
            )


@regla("E07", "Coordenada fuera de lugar (posible error de dígito)", ADV)
def e07(ctx):
    est = ctx.est
    limite = ctx.config["coordenadas"]["distancia_max_a_mediana_km"] * 1000
    puntos = [p for f in est.filas_datos if (p := ctx.punto(f))]
    if len(puntos) < 3:
        puntos = [p for f in est.filas_datos for c in ("", "_ini", "_fin") if (p := ctx.punto(f, c))]
    if not puntos:
        return
    med = (statistics.median(p[0] for p in puntos), statistics.median(p[1] for p in puntos))
    for f in est.filas_datos:
        for cual in ("", "_ini", "_fin"):
            p = ctx.punto(f, cual)
            if p is None:
                continue
            ce, cs = f"utm_e{cual}", f"utm_s{cual}"
            if not 100_000 <= p[0] <= 900_000:
                yield ctx.hallazgo(
                    "E07", est, f, ce, f"{NOMBRE_COORD[ce]} = {p[0]:.0f} no parece una coordenada UTM Este (6 dígitos).",
                    "Revisa si falta o sobra un dígito.",
                )
                continue
            if not 1_000_000 <= p[1] <= 10_000_000:
                yield ctx.hallazgo(
                    "E07", est, f, cs, f"{NOMBRE_COORD[cs]} = {p[1]:.0f} no parece una coordenada UTM Norte/Sur (7 dígitos).",
                    "Revisa si falta o sobra un dígito.",
                )
                continue
            d = distancia(p, med)
            if d > limite:
                de, ds = abs(p[0] - med[0]), abs(p[1] - med[1])
                col = ce if de >= ds else cs
                yield ctx.hallazgo(
                    "E07", est, f, col,
                    f"{NOMBRE_PUNTO[cual].capitalize()} {_xy(p)} está a {d / 1000:.1f} km de la mediana "
                    f"de las estaciones {_xy(med)}; la diferencia está sobre todo en la coordenada "
                    f"{'E' if col == ce else 'S'}.",
                    "Posible error de dígito o de huso: compara con el GPS.",
                    celdas_extra=[est.celda(f, ce if col == cs else cs)],
                )


@regla("E08", "Largo del transecto fuera de rango", ADV)
def e08(ctx):
    est = ctx.est
    cfg = ctx.config["transecto"]
    mn, mx = cfg["largo_min_m"], cfg["largo_max_m"]
    for f in est.filas_datos:
        largo = ctx.largo_transecto(f)
        if largo is None or mn <= largo <= mx:
            continue
        datos = {"valor": f"{largo:.0f} m",
                 "celdas_extra": [est.celda(f, c) for c in ("utm_s_ini", "utm_e_fin", "utm_s_fin")]}
        if largo < cfg["largo_minimo_creible_m"] or largo > cfg["largo_sospechoso_m"]:
            yield ctx.hallazgo(
                "E08", est, f, "utm_e_ini",
                f"El transecto mide {largo:.0f} m en línea recta entre inicio y fin (lo habitual es {mn}–{mx} m): "
                "con esa diferencia lo más probable es un error en una coordenada.",
                "Revisa las coordenadas de inicio y fin con el GPS o el track.", **datos,
            )
        elif largo < mn:
            yield ctx.hallazgo(
                "E08", est, f, "utm_e_ini",
                f"Transecto corto: {largo:.0f} m en línea recta entre inicio y fin (lo habitual es {mn}–{mx} m).",
                f"Si no se pudo recorrer completo por el terreno, está bien: el área se calcula con el largo real "
                f"({largo:.0f} m × ancho), así la densidad no se distorsiona. Si el recorrido no fue recto, el "
                "largo real es mayor (mídelo en el track). Si no, revisa las coordenadas.",
                severidad=INFO, justificaciones=["No se pudo recorrer completo (terreno)"], **datos,
            )
        else:
            yield ctx.hallazgo(
                "E08", est, f, "utm_e_ini",
                f"Transecto más largo de lo habitual: {largo:.0f} m en línea recta entre inicio y fin ({mn}–{mx} m).",
                "Si de verdad se recorrió ese largo, está bien: el área se calcula con el largo real. No lo "
                f"recortes a {mx} m: si se contó fauna en todo el recorrido, dividir por un largo menor infla "
                "la densidad. Si no, revisa las coordenadas.",
                severidad=INFO, justificaciones=["Se recorrió ese largo"], **datos,
            )


@regla("E09", "Coordenadas idénticas a las de otra estación (copy-paste)", CRIT)
def e09(ctx):
    est = ctx.est
    puntos = defaultdict(list)
    for f in est.filas_datos:
        for cual in ("", "_ini", "_fin"):
            p = ctx.punto(f, cual)
            if p is not None:
                puntos[p].append((f, cual))
    for p, lista in puntos.items():
        if len({f for f, _ in lista}) < 2:
            continue
        for f, cual in lista:
            otros = [(g, c) for g, c in lista if g != f]
            desc = "; ".join(f"{NOMBRE_PUNTO_AL[c]} de {ctx.id_de_estacion(g)} (fila {g})" for g, c in otros)
            yield ctx.hallazgo(
                "E09", est, f, f"utm_e{cual}",
                f"{NOMBRE_PUNTO[cual].capitalize()} {_xy(p)} es idéntico {desc}: probable copy-paste.",
                "Corrige con la coordenada real tomada en terreno; con la copiada, los largos de "
                "transecto y las distancias de captura quedan mal.",
                valor=f"{p[0]:.0f}, {p[1]:.0f}",
                celdas_extra=[est.celda(f, f"utm_s{cual}")]
                + [est.celda(g, f"utm_{e}{c}") for g, c in otros for e in ("e", "s")],
            )


@regla("E10", "Punto de la estación lejos del punto medio del transecto", ADV)
def e10(ctx):
    est = ctx.est
    cfg = ctx.config["transecto"]
    limite, sospechosa = cfg["distancia_max_estacion_a_punto_medio_m"], cfg["distancia_sospechosa_estacion_m"]
    for f in est.filas_datos:
        p, m = ctx.punto(f), ctx.punto_medio(f)
        if p is None or m is None:
            continue
        d = distancia(p, m)
        if d <= limite:
            continue
        texto = (f"El punto de la estación {_xy(p)} está a {d:.0f} m del punto medio del transecto {_xy(m)} "
                 f"(lo habitual es hasta {limite} m)")
        if d > sospechosa:
            yield ctx.hallazgo(
                "E10", est, f, "utm_e", texto + ": a esa distancia parece una coordenada mal escrita.",
                "Revisa la coordenada de la estación o las de inicio/fin.",
                celdas_extra=[est.celda(f, "utm_s")],
            )
        else:
            yield ctx.hallazgo(
                "E10", est, f, "utm_e", texto + ".",
                "Si al punto no se podía llegar y la coordenada es donde se pudo llegar, está bien: déjalo así "
                "y menciónalo en el informe (punto reubicado por acceso). Si no, revisa la coordenada.",
                severidad=INFO, justificaciones=["No se pudo llegar al punto (terreno)"],
                celdas_extra=[est.celda(f, "utm_s")],
            )


@regla("E11", "Metodologías con valores distintos de 1/0", ADV, auto_corregible=True)
def e11(ctx):
    est = ctx.est
    cfg = ctx.config["metodologias"]
    si = {clave(x) for x in cfg["valores_si"]}
    no = {clave(x) for x in cfg["valores_no"]}
    columnas = [c for c in est.columnas if c.startswith("met_")]
    for f in est.filas_datos:
        for c in columnas:
            v = est.valor(f, c)
            if es_numero_real(v) and v in (0, 1):
                continue
            nombre = est.encabezados[c]
            if es_vacio(v):
                yield ctx.hallazgo(
                    "E11", est, f, c, f"'{nombre}' vacío: la plantilla pide 1 (se hizo) o 0 (no se hizo).",
                    "Se escribe 0 (no realizado).", correccion=0,
                )
            elif clave(v) in si:
                yield ctx.hallazgo(
                    "E11", est, f, c, f"'{nombre}' = '{mostrar(v)}' en vez de 1.", "Se escribe 1.", correccion=1
                )
            elif clave(v) in no:
                yield ctx.hallazgo(
                    "E11", est, f, c, f"'{nombre}' = '{mostrar(v)}' en vez de 0.", "Se escribe 0.", correccion=0
                )
            else:
                yield ctx.hallazgo(
                    "E11", est, f, c, f"'{nombre}' = '{mostrar(v)}' no se puede interpretar como 1/0.",
                    "Escribe 1 si la metodología se aplicó en la estación y 0 si no.",
                    acciones=[Accion("1 (se hizo)", 1), Accion("0 (no se hizo)", 0)],
                )


@regla("E12", "Estación sin registros", INFO)
def e12(ctx):
    exactas, flexibles = set(), set()
    for _, _, u in ctx.iter_registros():
        if u["est_fila"] is not None:
            exactas.add(u["est_fila"])
        if u["est_fila_flex"] is not None:
            flexibles.add(u["est_fila_flex"])
    for f in ctx.est.filas_datos:
        if f in exactas:
            continue
        extra = (
            " Hay registros cuyo ID coincide solo ignorando mayúsculas/espacios (ver R01)."
            if f in flexibles else ""
        )
        yield ctx.hallazgo(
            "E12", ctx.est, f, "id", f"La estación no tiene ningún registro.{extra}",
            "No la borres: es esfuerzo de muestreo y cuenta como 0 en densidades, riqueza por "
            "estación y curvas de acumulación.",
        )


@regla("E13", "Proyecto/Región/Temporada inconsistentes entre filas", ADV, auto_corregible=True)
def e13(ctx):
    est = ctx.est
    for c in ("proyecto", "region", "temporada"):
        if not est.tiene(c):
            continue
        valores = {f: est.valor(f, c) for f in est.filas_datos}
        conteo = Counter(texto_id(v) for v in valores.values() if not es_vacio(v))
        if not conteo:
            continue
        ranking = conteo.most_common()
        mayoritaria = ranking[0][0] if len(ranking) == 1 or ranking[0][1] > ranking[1][1] else None
        nombre = est.encabezados[c]
        if len(ranking) == 1 and all(not es_vacio(v) for v in valores.values()):
            continue
        for f, v in valores.items():
            if mayoritaria is not None and texto_id(v) == mayoritaria:
                continue
            if es_vacio(v):
                yield ctx.hallazgo(
                    "E13", est, f, c, f"{nombre} vacío.",
                    f"Completa con el valor del resto de las estaciones ('{mayoritaria}')."
                    if mayoritaria else f"Completa {nombre}.",
                    acciones=[Accion(f"Usar «{mayoritaria}»", mayoritaria)] if mayoritaria else (),
                )
                continue
            a = clave(v)
            b = clave(mayoritaria) if mayoritaria else ""
            claro = mayoritaria is not None and (
                a == b or (b.startswith(a) and len(a) >= 3) or SequenceMatcher(None, a, b).ratio() >= 0.85
            )
            otros = ", ".join(f"'{k}' ({n})" for k, n in ranking)
            if claro:
                yield ctx.hallazgo(
                    "E13", est, f, c,
                    f"{nombre} = '{mostrar(v)}' difiere del valor mayoritario '{mayoritaria}' (valores: {otros}).",
                    f"Se reemplaza por '{mayoritaria}' (variante truncada o con error de tipeo).",
                    correccion=mayoritaria,
                )
            else:
                yield ctx.hallazgo(
                    "E13", est, f, c,
                    f"{nombre} = '{mostrar(v)}' no coincide con el resto de las filas (valores: {otros}).",
                    "Unifica el valor; si de verdad son proyectos/temporadas distintos, sepáralos en planillas distintas.",
                    acciones=[Accion(f"Usar «{mayoritaria}»", mayoritaria)] if mayoritaria else (),
                )


@regla("E14", "Ladera vacía o fuera de la lista de validación", INFO)
def e14(ctx):
    est = ctx.est
    if not est.tiene("ladera"):
        return
    lista = ctx.libro.listas.get("ladera") or []
    exactos = set(lista)
    por_clave = {clave(x): x for x in lista}
    for f in est.filas_datos:
        v = est.valor(f, "ladera")
        if es_vacio(v):
            yield ctx.hallazgo("E14", est, f, "ladera", "Ladera de exposición vacía.",
                               f"Elige un valor de la lista: {', '.join(lista)}.", opciones=lista)
        elif lista and limpiar(v) not in exactos:
            sug = por_clave.get(clave(v))
            yield ctx.hallazgo(
                "E14", est, f, "ladera", f"Ladera '{mostrar(v)}' no está en la lista de 'validación datos'.",
                f"Usa '{sug}'." if sug else f"Elige un valor de la lista: {', '.join(lista)}.",
                acciones=[Accion(f"Usar «{sug}»", sug)] if sug else (), opciones=lista,
            )


@regla("E15", "Fila con datos pero sin ID Estación", ADV)
def e15(ctx):
    for f in ctx.est.filas_sin_id:
        yield ctx.hallazgo(
            "E15", ctx.est, f, "id",
            "La fila tiene datos (ambiente, coordenadas…) pero no tiene ID Estación: ningún registro se puede unir a ella.",
            "Completa el ID o borra la fila si sobra.",
        )
