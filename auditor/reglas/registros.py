"""Reglas sobre las hojas Registros y Registros TA."""

from __future__ import annotations

import difflib
import re
import statistics
from collections import Counter, defaultdict
from datetime import date, time, timedelta

from ..contexto import distancia, texto_id
from ..hallazgos import Accion, Severidad, regla
from ..modelo import HOJA_TA
from ..texto import (
    a_fecha,
    a_numero,
    clave,
    clave_flexible,
    describir_espacios,
    en_rango_horario,
    es_numero_real,
    es_vacio,
    fecha_a_excel,
    hora_desde_texto,
    limpiar,
    mostrar,
    numero_para_celda,
)

CRIT, ADV, INFO = Severidad.CRITICO, Severidad.ADVERTENCIA, Severidad.INFO


def _parecidos(texto: str, opciones: dict[str, str], n: int = 3) -> list[str]:
    """Hasta n opciones parecidas (opciones: clave -> forma original)."""
    claves = difflib.get_close_matches(clave(texto), list(opciones), n=n, cutoff=0.6)
    return [opciones[c] for c in claves]


def _nombre_y_especie(ctx, fila_bd: int) -> str:
    return f"{texto_id(ctx.bd.valor(fila_bd, 'nombre_comun'))} ({ctx.cientifico_bd(fila_bd)})"


@regla("R01", "ID Estación no existe en Estaciones", CRIT)
def r01(ctx):
    ids = {clave(texto_id(ctx.est.valor(f, "id"))): texto_id(ctx.est.valor(f, "id")) for f in ctx.est.filas_datos}
    for t, f, u in ctx.iter_registros():
        if u["est_fila"] is not None:
            continue
        crudo = t.valor(f, "id")
        if u["est_fila_flex"] is not None:
            real = texto_id(ctx.est.valor(u["est_fila_flex"], "id"))
            detalle = ", ".join(describir_espacios(crudo)) or "mayúsculas"
            solo_mayus = limpiar(crudo) == texto_id(crudo)
            efecto = (
                "Excel lo une (MATCH no distingue mayúsculas), pero un join en R/pandas no."
                if solo_mayus else "Ni Excel ni R lo unen: los espacios sí cuentan."
            )
            yield ctx.hallazgo(
                "R01", t, f, "id",
                f"El ID '{mostrar(crudo)}' coincide con la estación '{real}' solo ignorando {detalle}. {efecto}",
                f"Escríbelo igual que en Estaciones: '{real}'.",
            )
        else:
            parecidos = _parecidos(texto_id(crudo), ids)
            yield ctx.hallazgo(
                "R01", t, f, "id",
                f"El ID '{mostrar(crudo)}' no existe en la hoja Estaciones: las columnas naranjas de la "
                "fila dan #N/A y el registro queda sin ambiente, coordenadas ni esfuerzo.",
                "Agrega la estación a la hoja Estaciones o corrige el ID"
                + (f" (¿{' / '.join(parecidos)}?)." if parecidos else "."),
            )


@regla("R02", "Nombre común no existe en nombres BD", CRIT)
def r02(ctx):
    opciones = {}
    for c, filas in ctx.bd_clave.items():
        opciones.setdefault(c, texto_id(ctx.bd.valor(filas[0], "nombre_comun")))
    en_base = defaultdict(set)  # género -> {(nombre, estación)}
    for _, _, u in ctx.iter_registros():
        if u["especie"]:
            en_base[clave(u["especie"].split(" ")[0])].add((u["nombre_bd"], u["especie"], u["id"]))
    for t, f, u in ctx.iter_registros():
        if u["bd_fila_flex"] is not None or u["n_candidatos_bd"]:
            continue
        nombre = t.valor(f, "nombre_comun")
        if es_vacio(nombre):
            yield ctx.hallazgo(
                "R02", t, f, "nombre_comun", "Nombre común vacío: el registro no tiene especie.",
                "Completa el nombre común tal como está en 'nombres BD'.",
            )
            continue
        partes = []
        parecidos = _parecidos(texto_id(nombre), opciones)
        candidatos = list(parecidos)
        if parecidos:
            partes.append("Parecidos en nombres BD: " + "; ".join(parecidos) + ".")
        genero = limpiar(nombre).split(" ")[0]
        congeneres = ctx.bd_por_genero.get(clave(genero), [])
        if congeneres:
            partes.append(
                f"Especies del género {genero} en nombres BD: "
                + "; ".join(_nombre_y_especie(ctx, c) for c in congeneres[:6]) + "."
            )
            presentes = sorted(en_base.get(clave(genero), []))
            candidatos = [n for n, _, _ in presentes] + [texto_id(ctx.bd.valor(c, "nombre_comun")) for c in congeneres] + candidatos
            if presentes:
                partes.append(
                    "En esta base ya está: "
                    + "; ".join(f"{n} ({e}) en {est}" for n, e, est in presentes) + "."
                )
            partes.append(
                f"Si no se pudo identificar a especie, agrega a nombres BD una entrada genérica "
                f"(p. ej. '{genero} sp.') o identifícalo con las fotos."
            )
        yield ctx.hallazgo(
            "R02", t, f, "nombre_comun",
            f"'{mostrar(nombre)}' no está en 'nombres BD' (ni ignorando mayúsculas): Nombre científico "
            "y Clase dan #N/A y el registro no entra a ningún análisis por especie.",
            " ".join(partes) or "Corrige el nombre o agrégalo a 'nombres BD'.",
            acciones=[Accion(f"Usar «{n}»", n) for n in dict.fromkeys(candidatos)][:5],
        )


@regla("R03", "Nombre común existe en nombres BD solo ignorando mayúsculas/espacios", CRIT, auto_corregible=True)
def r03(ctx):
    for t, f, u in ctx.iter_registros():
        if u["bd_fila"] is not None or not u["n_candidatos_bd"]:
            continue
        nombre = t.valor(f, "nombre_comun")
        if u["bd_fila_flex"] is None:
            opciones = sorted({_nombre_y_especie(ctx, c) for c in ctx.bd_clave[clave(texto_id(nombre))]})
            yield ctx.hallazgo(
                "R03", t, f, "nombre_comun",
                f"'{mostrar(nombre)}' coincide (ignorando mayúsculas/espacios) con varias entradas de "
                f"nombres BD de distinta especie: {'; '.join(opciones)}.",
                "Elige la entrada correcta y escríbela exactamente igual.",
                acciones=[
                    Accion(f"Usar «{_nombre_y_especie(ctx, c)}»", texto_id(ctx.bd.valor(c, "nombre_comun")))
                    for c in ctx.bd_clave[clave(texto_id(nombre))]
                ],
            )
            continue
        canonico = texto_id(ctx.bd.valor(u["bd_fila_flex"], "nombre_comun"))
        espacios = describir_espacios(nombre)
        diferencias = (["mayúsculas"] if limpiar(nombre) != limpiar(canonico) else []) + espacios
        efecto = (
            "Excel lo une (MATCH no distingue mayúsculas), pero un join en R/pandas falla y el registro queda sin especie."
            if not espacios else "Los espacios sobrantes hacen fallar tanto el MATCH de Excel como un join en R."
        )
        problema = (
            f"'{mostrar(nombre)}' coincide con '{canonico}' de nombres BD solo ignorando "
            f"{', '.join(diferencias)}. {efecto}"
        )
        if canonico == limpiar(canonico):
            yield ctx.hallazgo(
                "R03", t, f, "nombre_comun", problema,
                f"Se escribe '{canonico}', igual que en nombres BD.", correccion=canonico,
            )
        else:
            yield ctx.hallazgo(
                "R03", t, f, "nombre_comun", problema,
                f"El nombre en nombres BD tiene espacios sobrantes ('{mostrar(canonico)}'): corrige primero "
                "el diccionario (ver B01) y luego el registro.",
            )


@regla("R04", "Nombre genérico (sp./spp.)", ADV)
def r04(ctx):
    patrones = [re.compile(p, re.I) for p in ctx.config["nombres_genericos"]]
    identificadas = defaultdict(set)
    for _, _, u in ctx.iter_registros():
        esp = u["especie"] or ""
        if esp and not any(p.search(esp) or p.search(u["nombre"]) for p in patrones):
            identificadas[clave(esp.split(" ")[0])].add(f"{u['nombre_bd']} ({esp})")
    for t, f, u in ctx.iter_registros():
        nombre, esp = u["nombre"], u["especie"] or ""
        if not any(p.search(nombre) or (esp and p.search(esp)) for p in patrones):
            continue
        genero = (esp or nombre).split(" ")[0]
        mismas = sorted(identificadas.get(clave(genero), []))
        detalle = (
            f" En la base también hay especies identificadas del mismo género: {'; '.join(mismas)};"
            " el genérico podría ser una de ellas y no debe sumar riqueza."
            if mismas else ""
        )
        yield ctx.hallazgo(
            "R04", t, f, "nombre_comun",
            f"Nombre genérico '{nombre}'{f' ({esp})' if esp and esp != nombre else ''}: no está identificado a especie.{detalle}",
            "Identifícalo a especie con las fotos o repórtalo como morfoespecie, explicitando el "
            "criterio para riqueza y curvas de acumulación.",
        )


@regla("R05", "Mismo nombre científico escrito con distintos nombres comunes", ADV, auto_corregible=True)
def r05(ctx):
    por_especie = defaultdict(lambda: defaultdict(list))
    for t, f, u in ctx.iter_registros():
        if u["especie"]:
            por_especie[u["especie"]][texto_id(t.valor(f, "nombre_comun"))].append((t, f))
    for especie, variantes in por_especie.items():
        if len(variantes) < 2:
            continue
        oficiales = {
            texto_id(ctx.bd.valor(c, "nombre_comun"))
            for c in ctx.bd.filas_datos
            if clave(ctx.cientifico_bd(c)) == clave(especie)
        }
        resumen = ", ".join(f"'{mostrar(n)}' ({len(r)})" for n, r in sorted(variantes.items()))
        for nombre, regs in variantes.items():
            if nombre in oficiales:
                continue
            iguales = [o for o in oficiales if clave(o) == clave(nombre) and o == limpiar(o)]
            for t, f in regs:
                problema = (
                    f"{especie} está escrito de {len(variantes)} formas en la base: {resumen}. "
                    "Un análisis por nombre común lo contará como especies distintas."
                )
                if len(iguales) == 1:
                    yield ctx.hallazgo(
                        "R05", t, f, "nombre_comun", problema,
                        f"Se unifica a la forma de nombres BD: '{iguales[0]}'.", correccion=iguales[0],
                    )
                else:
                    yield ctx.hallazgo(
                        "R05", t, f, "nombre_comun", problema,
                        f"Usa una sola de las formas de nombres BD: {', '.join(sorted(oficiales))}.",
                        acciones=[Accion(f"Usar «{o}»", o) for o in sorted(oficiales)],
                    )


@regla("R06", "Abundancia vacía, no numérica, ≤0 o decimal", CRIT)
def r06(ctx):
    for t, f, u in ctx.iter_registros():
        if not t.tiene("abundancia"):
            continue
        v = t.valor(f, "abundancia")
        n = a_numero(v)
        if es_vacio(v):
            problema = "Abundancia vacía."
        elif n is None:
            presencia = " ('P' suele significar 'presencia')" if clave(v) in ("p", "presente", "presencia", "x") else ""
            problema = f"Abundancia no numérica: '{mostrar(v)}'{presencia}. R/PAST no pueden sumarla."
        elif n <= 0:
            problema = f"Abundancia {mostrar(n)} ≤ 0."
        elif not float(n).is_integer():
            problema = f"Abundancia decimal ({mostrar(n)}): debe ser un conteo de individuos."
        else:
            continue
        if u["dir_ind_calc"] == "Indirecto":
            sugerencia = (
                f"Es evidencia indirecta ({u['tipo_registro']}): entra a la riqueza pero no a la "
                "abundancia. Déjala vacía (NA) y exclúyela de densidades e índices de abundancia."
            )
            acciones = [Accion("Dejar vacía (NA)", None)] if not es_vacio(v) else []
        else:
            tipo = u["tipo_registro"].lower() or "registro directo"
            sugerencia = f"Es {tipo}: si no se contó, el mínimo defendible es 1 individuo."
            acciones = [Accion("Poner 1", 1)]
        yield ctx.hallazgo("R06", t, f, "abundancia", problema, sugerencia, acciones=acciones)


@regla("R07", "Abundancia atípica para la especie", INFO)
def r07(ctx):
    cfg = ctx.config["abundancia"]
    por_especie = defaultdict(list)
    for t, f, u in ctx.iter_registros():
        n = u["abundancia"]
        if u["especie"] and n is not None and n > 0:
            por_especie[u["especie"]].append((t, f, n))
    for especie, items in por_especie.items():
        if len(items) < cfg["min_registros_especie"]:
            continue
        valores = [n for _, _, n in items]
        q1, mediana, q3 = statistics.quantiles(valores, n=4, method="inclusive")
        limite = q3 + cfg["factor_iqr"] * max(q3 - q1, 1)
        pctl = None
        if len(valores) >= cfg["min_registros_percentil"]:
            pctl = statistics.quantiles(valores, n=100, method="inclusive")[cfg["percentil"] - 1]
        for t, f, n in items:
            if n > limite or (pctl is not None and n > pctl):
                yield ctx.hallazgo(
                    "R07", t, f, "abundancia",
                    f"Abundancia {mostrar(n)} atípica para {especie}: mediana {mostrar(mediana)}, "
                    f"Q3 {mostrar(q3)}, límite Q3 + {cfg['factor_iqr']}×IQR = {limite:.1f} (n = {len(valores)}).",
                    "Confirma el conteo (¿bandada?, ¿error de tipeo?); si es real, déjalo.",
                )


@regla("R08", "Especie doméstica", INFO)
def r08(ctx):
    cfg = ctx.config["domesticas"]
    cientificos = {clave(x) for x in cfg["nombres_cientificos"]}
    comunes = {clave(x) for x in cfg["nombres_comunes"]}
    for t, f, u in ctx.iter_registros():
        if clave(u["especie"] or "") in cientificos or clave(u["nombre"]) in comunes or clave(u["nombre_bd"] or "") in comunes:
            yield ctx.hallazgo(
                "R08", t, f, "nombre_comun",
                f"Especie doméstica ({u['especie'] or u['nombre']}): no es fauna silvestre y se excluye del análisis.",
                "Se mantiene en la planilla; en base_limpia_larga.csv sale con excluir_motivo = 'doméstica'.",
            )


@regla("R09", "Fecha vacía o fuera del rango de la campaña", ADV)
def r09(ctx):
    tol = ctx.config["fechas"]["tolerancia_dias"]
    validas = sorted(u["fecha"].toordinal() for _, _, u in ctx.iter_registros() if isinstance(u["fecha"], date))
    rango = None
    if validas:
        if len(validas) >= 2:
            cuantiles = statistics.quantiles(validas, n=20, method="inclusive")
            rango = (date.fromordinal(round(cuantiles[0])), date.fromordinal(round(cuantiles[-1])))
        else:
            rango = (date.fromordinal(validas[0]),) * 2
    for t, f, u in ctx.iter_registros():
        if not t.tiene("fecha"):
            continue
        v, d = t.valor(f, "fecha"), u["fecha"]
        if es_vacio(v):
            yield ctx.hallazgo("R09", t, f, "fecha", "Fecha vacía.", "Completa la fecha del registro.")
        elif d is None:
            yield ctx.hallazgo("R09", t, f, "fecha", f"Fecha no válida: '{mostrar(v)}'.", "Escribe la fecha como fecha de Excel (dd-mm-aaaa).")
        elif isinstance(v, str):
            yield ctx.hallazgo(
                "R09", t, f, "fecha", f"Fecha escrita como texto ('{mostrar(v)}'): R la leerá como texto.",
                f"Escríbela como fecha de Excel ({d:%d-%m-%Y}).",
            )
        elif rango and not (rango[0] - timedelta(days=tol) <= d <= rango[1] + timedelta(days=tol)):
            sugerencia = "Revisa día, mes y año."
            acciones = []
            if d.day <= 12:
                invertida = date(d.year, d.day, d.month)
                if rango[0] <= invertida <= rango[1]:
                    sugerencia = f"¿Se invirtieron día y mes? {invertida:%d-%m-%Y} cae dentro de la campaña."
                    acciones = [Accion(f"Usar {invertida:%d-%m-%Y}", fecha_a_excel(invertida))]
            yield ctx.hallazgo(
                "R09", t, f, "fecha",
                f"Fecha {d:%d-%m-%Y} a más de {tol} días del grueso de la campaña "
                f"({rango[0]:%d-%m-%Y} a {rango[1]:%d-%m-%Y} concentra el 90% de los registros).",
                sugerencia, acciones=acciones,
            )


@regla("R10", "Hora vacía", ADV)
def r10(ctx):
    camara = clave(ctx.config["horarios"]["metodologia_camara"])
    for t, f, u in ctx.iter_registros():
        if not t.tiene("hora"):
            continue
        v = t.valor(f, "hora")
        if es_vacio(v):
            sugerencia = (
                "Usa la hora de la foto (metadatos o nombre TimePhoto)."
                if clave(u["metodologia"]) == camara
                else "Completa la hora: sin ella no se pueden revisar los horarios de actividad."
            )
            yield ctx.hallazgo("R10", t, f, "hora", "Hora vacía.", sugerencia)
        elif u["hora"] is None:
            yield ctx.hallazgo("R10", t, f, "hora", f"Hora no válida: '{mostrar(v)}'.", "Escribe la hora como hh:mm.")


@regla("R11", "Aves en transecto fuera del horario de mayor actividad", INFO)
def r11(ctx):
    cfg = ctx.config["aves"]
    ini, fin = (hora_desde_texto(x) for x in cfg["horario_baja_actividad"])
    transecto = clave(ctx.config["horarios"]["metodologia_transecto"])
    for t in ctx.libro.registros:
        if t.hoja == HOJA_TA or not t.tiene("hora"):
            continue
        u = ctx.uniones[t.hoja]
        aves = u[(u["clase"] == cfg["clase"]) & (u["metodologia"].map(clave) == transecto)]
        con_hora = aves[aves["hora"].notna()]
        if con_hora.empty:
            continue
        en_rango = con_hora["hora"].map(lambda h: en_rango_horario(h, ini, fin))
        pct = 100 * en_rango.mean()
        por_est = (
            con_hora.assign(en=en_rango).groupby("id")["en"].agg(["sum", "count"]).sort_index()
        )
        mayoria = [f"{i} ({int(r['sum'])}/{int(r['count'])})" for i, r in por_est.iterrows() if r["sum"] > r["count"] / 2]
        ctx.estadisticas.setdefault("R11", {})[t.hoja] = {
            "pct": pct, "n_en_rango": int(en_rango.sum()), "n": len(con_hora),
            "rango": f"{ini:%H:%M}–{fin:%H:%M}", "estaciones_mayoria": mayoria,
            "n_estaciones": len(por_est),
        }
        if pct < cfg["umbral_pct"]:
            continue
        yield ctx.hallazgo(
            "R11", t, None, None,
            f"El {pct:.0f}% de los registros de aves en transecto ({int(en_rango.sum())} de {len(con_hora)}) "
            f"se hizo entre {ini:%H:%M} y {fin:%H:%M}, horario de baja actividad de aves. Estaciones con "
            f"la mayoría de sus registros de aves en ese horario ({len(mayoria)} de {len(por_est)}): {', '.join(mayoria)}.",
            "La detectabilidad de aves cae al mediodía: la riqueza y las abundancias pueden estar "
            "subestimadas. Decláralo como limitación en el informe o repite transectos temprano en la mañana.",
            celda=f"{t.columnas['hora']}{t.fila_encabezado}", valor=f"{pct:.0f}%",
        )


@regla("R12", "Hora incoherente con la metodología", ADV)
def r12(ctx):
    cfg = ctx.config["horarios"]
    noche = [hora_desde_texto(x) for x in cfg["nocturno"]]
    sherman = [hora_desde_texto(x) for x in cfg["sherman_revision"]]
    nocturnas = {clave(m) for m in cfg["metodologias_nocturnas"]}
    for t, f, u in ctx.iter_registros():
        h, m = u["hora"], clave(u["metodologia"])
        if h is None:
            continue
        if m in nocturnas and not en_rango_horario(h, *noche):
            yield ctx.hallazgo(
                "R12", t, f, "hora",
                f"{u['metodologia']} (nocturno) registrado a las {h:%H:%M}, en horario diurno "
                f"(se espera entre {noche[0]:%H:%M} y {noche[1]:%H:%M}).",
                "Revisa la hora (¿AM/PM?) o la metodología.",
            )
        elif m == clave(cfg["metodologia_sherman"]) and not en_rango_horario(h, *sherman):
            yield ctx.hallazgo(
                "R12", t, f, "hora",
                f"Captura en trampa Sherman a las {h:%H:%M}: las trampas se revisan entre "
                f"{sherman[0]:%H:%M} y {sherman[1]:%H:%M}.",
                "Revisa la hora o la metodología del registro.",
            )
        elif m == clave(cfg["metodologia_camara"]) and h == time(0, 0):
            yield ctx.hallazgo(
                "R12", t, f, "hora", "Cámara trampa con hora 00:00: suele indicar que no se registró la hora.",
                "Usa la hora de la foto.",
            )


@regla("R13", "Metodología o tipo de registro fuera de la lista de validación", ADV)
def r13(ctx):
    for clave_col, nombre in (("metodologia", "Metodología"), ("tipo_registro", "Tipo de registro")):
        lista = ctx.libro.listas.get(clave_col) or []
        if not lista:
            continue
        opciones = {clave(x): x for x in lista}
        for t, f, _u in ctx.iter_registros():
            if not t.tiene(clave_col):
                continue
            v = t.valor(f, clave_col)
            if es_vacio(v):
                yield ctx.hallazgo("R13", t, f, clave_col, f"{nombre} vacía.",
                                   f"Elige un valor de la lista: {', '.join(lista)}.", opciones=lista)
            elif limpiar(v) not in lista:
                parecidos = _parecidos(limpiar(v), opciones, n=1)
                yield ctx.hallazgo(
                    "R13", t, f, clave_col,
                    f"{nombre} '{mostrar(v)}' no está en la lista de 'validación datos'.",
                    f"¿'{parecidos[0]}'?" if parecidos else f"Usa un valor de la lista: {', '.join(lista)}.",
                    acciones=[Accion(f"Usar «{parecidos[0]}»", parecidos[0])] if parecidos else (),
                    opciones=lista,
                )
            elif describir_espacios(v):
                yield ctx.hallazgo(
                    "R13", t, f, clave_col,
                    f"{nombre} '{mostrar(v)}' tiene {', '.join(describir_espacios(v))}: R lo agrupa aparte.",
                    f"Escríbelo como en la lista: '{limpiar(v)}'.",
                    acciones=[Accion(f"Usar «{limpiar(v)}»", limpiar(v))], opciones=lista,
                )


@regla("R14", "Directo/Indirecto incoherente con el tipo de registro", ADV)
def r14(ctx):
    di = ctx.libro.directo_indirecto
    reconocidos = {clave(x): x for x in di.get("Directo", []) + di.get("Indirecto", [])}
    for t, f, u in ctx.iter_registros():
        if not t.tiene("dir_ind") or es_vacio(u["tipo_registro"]):
            continue
        calculado = u["dir_ind_calc"]
        v = t.valor(f, "dir_ind")
        es_f = bool(t.es_formula.at[f, "dir_ind"])
        if calculado == "REVISAR" and (es_f or es_vacio(v) or clave(v) == "revisar"):
            tipo = u["tipo_registro"]
            pista = ""
            for variante in (clave(tipo) + "s", clave(tipo)[:-1] if clave(tipo).endswith("s") else None):
                if variante and variante in reconocidos:
                    pista = (
                        f" La lista de 'validación datos' ofrece '{tipo}', pero la fórmula espera "
                        f"'{reconocidos[variante]}': es un error de la plantilla."
                    )
            yield ctx.hallazgo(
                "R14", t, f, "dir_ind",
                f"La fórmula Directo/Indirecto da 'REVISAR': no reconoce el tipo de registro '{tipo}'.{pista}",
                "Escribe a mano 'Directo' o 'Indirecto' en esta celda (la plantilla lo permite) o usa "
                "un tipo reconocido.",
                acciones=[Accion("Directo", "Directo"), Accion("Indirecto", "Indirecto")],
            )
        elif not es_f and not es_vacio(v) and calculado != "REVISAR" and clave(v) != clave(calculado):
            yield ctx.hallazgo(
                "R14", t, f, "dir_ind",
                f"Directo/Indirecto escrito a mano ('{mostrar(v)}') no coincide con el tipo de registro "
                f"'{u['tipo_registro']}' (corresponde '{calculado}').",
                f"Escribe '{calculado}' o restaura la fórmula.",
                acciones=[Accion(f"Usar «{calculado}»", calculado)],
            )


@regla("R15", "Registro duplicado", ADV)
def r15(ctx):
    vistos = {}
    for t, f, u in ctx.iter_registros():
        if u["fecha"] is None or u["hora"] is None:
            continue
        llave = (t.hoja, clave(u["id"]), clave(u["especie"] or u["nombre"]), u["fecha"], u["hora"], u["abundancia"])
        if llave in vistos:
            primera = vistos[llave]
            yield ctx.hallazgo(
                "R15", t, f, "nombre_comun",
                f"Registro repetido: misma estación, especie, fecha, hora y abundancia que la fila {primera}.",
                "Si es el mismo individuo (p. ej. visto y oído), deja uno; si son individuos distintos, "
                "súmalos en un registro o corrige la hora.",
                celdas_extra=[t.celda(primera, "nombre_comun")],
            )
        else:
            vistos[llave] = f


@regla("R16", "Coordenada de captura lejos de su estación", ADV)
def r16(ctx):
    limite = ctx.config["captura"]["distancia_max_m"]
    referencias = {g: p for g in ctx.est.filas_datos if (p := ctx.punto_referencia(g))}
    for t, f, u in ctx.iter_registros():
        if not t.tiene("utm_e_captura"):
            continue
        ve, vs = t.valor(f, "utm_e_captura"), t.valor(f, "utm_s_captura")
        if es_vacio(ve) and es_vacio(vs):
            continue
        e, s = a_numero(ve), a_numero(vs)
        if e is None or s is None:
            mal = "utm_e_captura" if e is None else "utm_s_captura"
            yield ctx.hallazgo(
                "R16", t, f, mal, "Coordenada de captura incompleta o no numérica.",
                "Completa ambas coordenadas (E y S) como números.",
            )
            continue
        g = u["est_fila_flex"]
        if g is None or g not in referencias:
            continue
        d = distancia((e, s), referencias[g])
        if d <= limite:
            continue
        cercana = min(referencias, key=lambda x: distancia((e, s), referencias[x]))
        pista = ""
        if cercana != g:
            pista = (
                f" La estación más cercana es {ctx.id_de_estacion(cercana)} "
                f"({distancia((e, s), referencias[cercana]):.0f} m)."
            )
        yield ctx.hallazgo(
            "R16", t, f, "utm_e_captura",
            f"La captura ({e:.0f}, {s:.0f}) está a {d:.0f} m de su estación {u['id']} "
            f"(máximo {limite} m).{pista}",
            "Revisa la coordenada de captura o el ID de estación.",
            celdas_extra=[t.celda(f, "utm_s_captura")],
        )


def _patron_duda(termino: str) -> re.Pattern:
    if re.fullmatch(r"\w[\w ]*", termino):
        return re.compile(rf"(?<!\w){re.escape(termino)}(?!\w)", re.I)
    return re.compile(re.escape(termino), re.I)


@regla("R17", "Texto con duda en Observaciones o Fotos", INFO)
def r17(ctx):
    patrones = [(x, _patron_duda(x)) for x in ctx.config["texto_duda"]]
    for t, f, _u in ctx.iter_registros():
        for col in ("observaciones", "fotos"):
            if not t.tiene(col):
                continue
            v = t.valor(f, col)
            if not isinstance(v, str):
                continue
            hits = [x for x, p in patrones if p.search(v)]
            if hits:
                yield ctx.hallazgo(
                    "R17", t, f, col,
                    f"{t.encabezados[col]} con duda: '{mostrar(v)}' (contiene: {', '.join(repr(h) for h in hits)}).",
                    "Resuelve la duda (identificación, quién tiene la foto, ubicación) y deja el texto final sin signos de duda.",
                )


_TOKEN_FOTO = re.compile(r"[A-Za-z]+_?\d[\w]*")


@regla("R18", "Fotos con separadores o códigos inconsistentes", INFO)
def r18(ctx):
    patrones = [re.compile(p) for p in ctx.config["fotos"]["patrones"]]
    celdas = []
    for t, f, u in ctx.iter_registros():
        if not t.tiene("fotos"):
            continue
        v = t.valor(f, "fotos")
        if es_vacio(v):
            continue
        s = limpiar(v)
        toks = list(_TOKEN_FOTO.finditer(s))
        seps = [s[a.end() : b.start()].strip() or " " for a, b in zip(toks, toks[1:])]
        celdas.append((t, f, u, v, toks, seps))
    usos = Counter(sep for *_, seps in celdas for sep in set(seps))
    preferencia = [",", ";", " ", "-", ":"]  # desempate: la coma es lo habitual
    dominante = min(usos, key=lambda s: (-usos[s], preferencia.index(s) if s in preferencia else 99)) if usos else None
    for t, f, u, v, toks, seps in celdas:
        problemas = []
        if not toks:
            problemas.append("no contiene códigos de foto reconocibles")
        otros = sorted(set(seps) - {dominante}) if dominante else []
        if otros:
            texto = ", ".join(f"'{x}'" for x in otros)
            rango = " ('-' puede leerse como rango o como lista)" if "-" in otros else ""
            problemas.append(f"usa el separador {texto} en vez del dominante '{dominante}'{rango}")
        malos = [m.group() for m in toks if not any(p.match(m.group()) for p in patrones)]
        if malos:
            problemas.append(f"códigos que no siguen los patrones esperados: {', '.join(malos)}")
        for m in toks:
            tp = re.match(r"^TimePhoto_(\d{8})_\d{6}$", m.group())
            if tp and u["fecha"]:
                d = a_fecha(f"{tp.group(1)[6:]}/{tp.group(1)[4:6]}/{tp.group(1)[:4]}")
                if d and d != u["fecha"]:
                    problemas.append(f"{m.group()} es del {d:%d-%m-%Y}, pero el registro es del {u['fecha']:%d-%m-%Y}")
        if problemas:
            yield ctx.hallazgo(
                "R18", t, f, "fotos", f"Fotos '{mostrar(v)}': " + "; ".join(problemas) + ".",
                f"Usa un solo separador ('{dominante or ','}') y los códigos tal como los genera la cámara.",
            )


@regla("R19", "Clase incompatible con la metodología", ADV)
def r19(ctx):
    permitidas = {
        clave(m): {clave(ctx.clase_normalizada(c)) for c in clases}
        for m, clases in ctx.config["metodologias"]["clases_permitidas"].items()
    }
    nombres = {clave(m): clases for m, clases in ctx.config["metodologias"]["clases_permitidas"].items()}
    for t, f, u in ctx.iter_registros():
        m = clave(u["metodologia"])
        if m in permitidas and u["clase"] and clave(u["clase"]) not in permitidas[m]:
            yield ctx.hallazgo(
                "R19", t, f, "metodologia" if t.tiene("metodologia") else "nombre_comun",
                f"{u['clase']} ({u['especie']}) registrado con '{u['metodologia']}', metodología para "
                f"{', '.join(nombres[m])}.",
                "Revisa la metodología (¿fue un registro oportunista?) o la especie.",
            )


@regla("R20", "Metodología del registro no activada en Estaciones", INFO)
def r20(ctx):
    cfg = ctx.config["metodologias"]
    si = {clave(x) for x in cfg["valores_si"]}
    por_encabezado = {clave(v): k for k, v in ctx.est.encabezados.items() if k.startswith("met_")}
    columna = {clave(m): por_encabezado.get(clave(enc)) for m, enc in cfg["columnas_estaciones"].items()}
    for t, f, u in ctx.iter_registros():
        k = columna.get(clave(u["metodologia"]))
        g = u["est_fila_flex"]
        if k is None or g is None:
            continue
        v = ctx.est.valor(g, k)
        if (es_numero_real(v) and v == 1) or clave(v) in si:
            continue
        yield ctx.hallazgo(
            "R20", t, f, "metodologia",
            f"Registro de '{u['metodologia']}', pero en Estaciones la estación {u['id']} no tiene esa "
            f"metodología marcada ('{ctx.est.encabezados[k]}' = {mostrar(v)} en {ctx.est.celda(g, k)}).",
            f"Marca 1 en Estaciones!{ctx.est.celda(g, k)} o corrige la metodología del registro; si no, "
            "el esfuerzo de esa metodología queda mal contado.",
            acciones=[Accion(f"Marcar 1 en Estaciones!{ctx.est.celda(g, k)}", 1, ctx.est.hoja, ctx.est.celda(g, k))],
        )


@regla("R21", "Comportamiento fuera de la lista de validación", INFO)
def r21(ctx):
    lista = ctx.libro.listas.get("comportamiento") or []
    if not lista:
        return
    opciones = {clave(x): x for x in lista}
    for t, f, _u in ctx.iter_registros():
        if t.hoja == HOJA_TA or not t.tiene("comportamiento"):
            continue
        v = t.valor(f, "comportamiento")
        if es_vacio(v):
            continue
        if bool(t.es_formula.at[f, "comportamiento"]):
            # Valor puesto por la fórmula de ayuda: si no está en la lista lo reporta T01 una vez.
            if clave(v) == "completar":
                yield ctx.hallazgo(
                    "R21", t, f, "comportamiento",
                    "Comportamiento sin completar (la fórmula de ayuda muestra 'COMPLETAR').",
                    f"Elige un valor de la lista: {', '.join(lista)}.", opciones=lista,
                )
        elif limpiar(v) not in lista:
            parecido = _parecidos(limpiar(v), opciones, n=1)
            yield ctx.hallazgo(
                "R21", t, f, "comportamiento", f"Comportamiento '{mostrar(v)}' no está en la lista de 'validación datos'.",
                f"¿'{parecido[0]}'?" if parecido else "Usa un valor de la lista o agrégalo a la lista dejando constancia.",
                acciones=[Accion(f"Usar «{parecido[0]}»", parecido[0])] if parecido else (), opciones=lista,
            )


@regla("R22", "Número guardado como texto (captura o abundancia)", ADV, auto_corregible=True)
def r22(ctx):
    for t, f, _u in ctx.iter_registros():
        for col in ("utm_e_captura", "utm_s_captura", "abundancia"):
            if not t.tiene(col):
                continue
            v = t.valor(f, col)
            if not isinstance(v, str) or es_vacio(v):
                continue
            n = a_numero(v)
            if n is None:
                continue  # texto de verdad: lo reportan R06 / R16
            detalle = describir_espacios(v)
            yield ctx.hallazgo(
                "R22", t, f, col,
                f"{t.encabezados[col]} guardada como texto{f' ({chr(44).join(detalle)})' if detalle else ''}: "
                "R/PAST la leen como texto o NA.",
                f"Se convierte al número {numero_para_celda(n)}.", correccion=numero_para_celda(n),
            )


@regla("R23", "Observación menciona otra metodología", INFO)
def r23(ctx):
    claves_met = ctx.config["metodologias"]["palabras_clave"]
    for t, f, u in ctx.iter_registros():
        if not t.tiene("observaciones") or t.hoja == HOJA_TA:
            continue
        v = t.valor(f, "observaciones")
        if es_vacio(v):
            continue
        obs = clave_flexible(v)
        for palabra, metodologia in claves_met.items():
            if re.search(rf"\b{re.escape(clave_flexible(palabra))}", obs) and not clave_flexible(
                u["metodologia"]
            ).startswith(clave_flexible(metodologia)):
                yield ctx.hallazgo(
                    "R23", t, f, "observaciones",
                    f"La observación '{mostrar(v)}' menciona {metodologia}, pero la metodología del "
                    f"registro es '{u['metodologia']}'.",
                    f"Si el registro fue en {metodologia}, corrige la metodología (afecta el esfuerzo); "
                    "si fue en el transecto, aclara la observación.",
                )
                break


@regla("R24", "Fila con datos pero sin ID Estación", ADV)
def r24(ctx):
    for t in ctx.libro.registros:
        for f in t.filas_sin_id:
            yield ctx.hallazgo(
                "R24", t, f, "id",
                "La fila tiene datos (especie, fecha…) pero no tiene ID Estación: la plantilla no la "
                "considera registro y se pierde del análisis.",
                "Completa el ID Estación.",
            )
