"""reporte.md y archivos CSV."""

from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from .contexto import Contexto
from .hallazgos import REGLAS, Hallazgo, Severidad
from .modelo import Libro, tabla_mapeo
from .motor import Cambio, Puntaje, formula_puntaje
from .reglas.registros import es_domestica, es_exotica
from .texto import clave, mostrar
from .verificacion import Verificacion


def _orden_natural(s: str):
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", s or "")]


def _md(texto) -> str:
    return str(texto).replace("|", "\\|").replace("\n", " ")


# --------------------------------------------------------------------------- CSV


def escribir_cambios_csv(ruta: Path, cambios: list[Cambio]) -> None:
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["hoja", "celda", "valor_anterior", "valor_nuevo", "regla", "valor_anterior_visible"])
        for c in sorted(cambios, key=lambda c: (c.hoja, c.fila, c.celda)):
            anterior = "" if c.valor_anterior is None else c.valor_anterior
            w.writerow([c.hoja, c.celda, anterior, c.valor_nuevo, "+".join(c.reglas), mostrar(c.valor_anterior)])


def escribir_hallazgos_csv(
    ruta: Path, hallazgos: list[Hallazgo], resueltos: set[str], pendientes: set, decisiones: dict[str, str] | None = None
) -> None:
    decisiones = decisiones or {}
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id_regla", "severidad", "hoja", "celda", "fila", "id_estacion", "valor_actual", "problema",
                    "sugerencia", "auto_corregible", "aplicado", "valor_nuevo", "pendiente", "decision"])
        for h in hallazgos:
            w.writerow([
                h.id_regla, h.severidad.etiqueta, h.hoja, h.celda or "", h.fila or "", h.id_estacion or "",
                "" if h.valor_actual is None else mostrar(h.valor_actual), h.problema, h.sugerencia,
                "sí" if h.auto_corregible else "no",
                "sí" if h.clave() in resueltos else "no",
                mostrar(h.correccion.valor_nuevo) if h.correccion else "",
                "sí" if h.firma() in pendientes else "no",
                decisiones.get(h.clave(), ""),
            ])


COLUMNAS_BASE = [
    "estacion", "ambiente", "clase", "nombre_cientifico", "nombre_comun", "metodologia", "tipo_registro",
    "abundancia", "fecha", "hora", "directo_indirecto", "usar_en_abundancia", "largo_transecto_m",
    "ancho_transecto_m", "area_muestreada_m2", "origen", "excluir_motivo", "hallazgos_pendientes", "hoja", "fila",
]


def filas_base_larga(libro: Libro, ctx: Contexto, hallazgos: list[Hallazgo], config: dict) -> list[dict]:
    """Tabla larga para R/PAST con las uniones rehechas en código. No se borran filas."""
    anchos = {clave(k): v for k, v in config["ancho_transecto_m"].items()}
    transecto = clave(config["horarios"]["metodologia_transecto"])
    por_fila = defaultdict(set)
    for h in hallazgos:
        if h.fila is not None:
            por_fila[(h.hoja, h.fila)].add(h.id_regla)
    out = []
    for t, f, u in ctx.iter_registros():
        g = u["est_fila_flex"]
        motivos = []
        domestica = es_domestica(config, u["especie"], u["nombre"], u["nombre_bd"])
        exotica = es_exotica(config, u["especie"], u["nombre"], u["nombre_bd"])
        origen = "doméstica" if domestica else "exótica" if exotica else ""
        if domestica and config["domesticas"].get("excluir_del_analisis", True):
            motivos.append("doméstica")
        if exotica and config["exoticas"].get("excluir_del_analisis", False):
            motivos.append("exótica")
        if g is None:
            motivos.append("estación no existe en Estaciones")
        if not u["especie"]:
            motivos.append("especie no está en nombres BD")
        n = u["abundancia"]
        abundancia_ok = n is not None and n > 0 and float(n).is_integer()
        dir_ind = u["dir_ind_calc"]
        if dir_ind == "REVISAR" and t.tiene("dir_ind") and clave(t.valor(f, "dir_ind")) in ("directo", "indirecto"):
            dir_ind = mostrar(t.valor(f, "dir_ind"))
        largo = ctx.largo_transecto(g) if g is not None and clave(u["metodologia"]) == transecto else None
        ancho = anchos.get(clave(u["clase"] or "")) if largo is not None else None
        out.append({
            "estacion": u["id"],
            "ambiente": mostrar(ctx.est.valor(g, "ambiente")) if g is not None and ctx.est.valor(g, "ambiente") is not None else "",
            "clase": u["clase"] or "",
            "nombre_cientifico": u["especie"] or "",
            "nombre_comun": u["nombre_bd"] or u["nombre"],
            "metodologia": u["metodologia"],
            "tipo_registro": u["tipo_registro"],
            "abundancia": int(n) if abundancia_ok else "",
            "fecha": u["fecha"].isoformat() if isinstance(u["fecha"], date) else "",
            "hora": u["hora"].strftime("%H:%M") if u["hora"] else "",
            "directo_indirecto": dir_ind or "",
            "usar_en_abundancia": "si" if abundancia_ok and clave(dir_ind or "") != "indirecto" and not motivos else "no",
            "largo_transecto_m": round(largo, 1) if largo is not None else "",
            "ancho_transecto_m": ancho if ancho is not None else "",
            "area_muestreada_m2": round(largo * ancho) if largo is not None and ancho is not None else "",
            "origen": origen,
            "excluir_motivo": "; ".join(motivos),
            "hallazgos_pendientes": ";".join(sorted(por_fila.get((t.hoja, f), set()))),
            "hoja": t.hoja,
            "fila": f,
        })
    return out


def resumen_origen(ctx: Contexto, config: dict) -> dict[str, list[dict]]:
    """Especies domésticas y exóticas de la planilla, con sus registros, individuos y estaciones."""
    grupos: dict[str, dict] = {"domesticas": {}, "exoticas": {}}
    for _, _, u in ctx.iter_registros():
        if es_domestica(config, u["especie"], u["nombre"], u["nombre_bd"]):
            grupo = grupos["domesticas"]
        elif es_exotica(config, u["especie"], u["nombre"], u["nombre_bd"]):
            grupo = grupos["exoticas"]
        else:
            continue
        e = grupo.setdefault(clave(u["especie"] or u["nombre_bd"] or u["nombre"]), {
            "nombre_comun": u["nombre_bd"] or u["nombre"], "nombre_cientifico": u["especie"] or "",
            "registros": 0, "individuos": 0, "estaciones": set(),
        })
        e["registros"] += 1
        n = u["abundancia"]
        if n is not None and n > 0 and float(n).is_integer():
            e["individuos"] += int(n)
        if u["id"]:
            e["estaciones"].add(u["id"])
    return {
        k: [{**e, "estaciones": sorted(e["estaciones"], key=_orden_natural)}
            for e in sorted(g.values(), key=lambda e: (-e["registros"], e["nombre_comun"]))]
        for k, g in grupos.items()
    }


def _describir_especie(e: dict) -> str:
    nombre = e["nombre_comun"] + (f" (*{e['nombre_cientifico']}*)" if e["nombre_cientifico"] else "")
    registros = f"{e['registros']} registro" + ("s" if e["registros"] != 1 else "")
    return f"{nombre}: {registros}, {e['individuos']} ind., {', '.join(e['estaciones'])}"


def _lineas_origen(origen: dict[str, list[dict]], config: dict) -> list[str]:
    if not any(origen.values()):
        return []
    criterio = (
        "Criterio de la LdB de XPE: el ganado, los perros y los gatos se registran en terreno, pero no se "
        "consideran en la lista de especies ni en los análisis por ser domésticos; las especies exóticas "
        "(conejo, liebre, gorrión, codorniz, guarén…) sí entran, marcadas como «Exótico»."
    )
    L = ["## Especies domésticas y exóticas", "", criterio, "",
         "- **En la planilla que entregas** van todos los registros: el auditor no borra ninguno."]
    for grupo, titulo in (("domesticas", "Domésticas"), ("exoticas", "Exóticas")):
        especies = origen.get(grupo) or []
        if not especies:
            continue
        excluir = config[grupo].get("excluir_del_analisis", grupo == "domesticas")
        efecto = (f"**fuera del análisis** (`excluir_motivo = {'doméstica' if grupo == 'domesticas' else 'exótica'}`)"
                  if excluir else "**dentro del análisis**")
        L.append(f"- **{titulo}** — en `base_limpia_larga.csv` quedan {efecto}: "
                 + "; ".join(_describir_especie(e) for e in especies) + ".")
    L.append("")
    return L


def escribir_base_larga(ruta: Path, filas: list[dict]) -> None:
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS_BASE)
        w.writeheader()
        w.writerows(filas)


# --------------------------------------------------------------------------- reporte.md


def _resumen_cambios(cambios: list[Cambio]) -> list[str]:
    lineas = []
    por_regla = defaultdict(list)
    for c in cambios:
        por_regla["+".join(c.reglas)].append(c)
    for reglas, cs in sorted(por_regla.items()):
        titulo = " / ".join(REGLAS[r].titulo for r in reglas.split("+") if r in REGLAS)
        transiciones = Counter((mostrar(c.valor_anterior), mostrar(c.valor_nuevo)) for c in cs)
        detalle = ", ".join(f"«{a}» → «{b}» ({n})" for (a, b), n in transiciones.most_common())
        celdas = ", ".join(f"{c.hoja}!{c.celda}" for c in cs[:12]) + (" …" if len(cs) > 12 else "")
        lineas.append(f"- **{reglas}** {titulo} — {len(cs)} celda(s): {detalle}. Celdas: {celdas}.")
    return lineas


def _linea_hallazgo(h: Hallazgo) -> str:
    donde = f"`{h.hoja}!{h.celda}`" if h.celda else f"`{h.hoja}`"
    return f"- {h.severidad.emoji} **{h.id_regla}** {donde} — {_md(h.problema)} → {_md(h.sugerencia)}"


def _check(ok) -> str:
    return "✅" if ok else ("—" if ok is None else "❌")


def escribir_reporte(
    ruta: Path, *, nombre: str, libro: Libro, antes: Puntaje, despues: Puntaje,
    hallazgos_antes: list[Hallazgo], hallazgos_despues: list[Hallazgo], cambios: list[Cambio],
    avisos_cambios: list[str], verificacion: Verificacion | None, estadisticas: dict, config: dict,
    solo_reporte: bool, archivos: list[str], decisiones: dict | None = None, origen: dict | None = None,
) -> None:
    """decisiones: clave -> (hallazgo, Decision) tomadas en la aplicación; origen: resumen_origen()."""
    decisiones = decisiones or {}
    manuales = [c for c in cambios if any(r.startswith("MANUAL") for r in c.reglas)]
    automaticos = [c for c in cambios if c not in manuales]
    L: list[str] = []
    L.append(f"# Auditoría de planilla de fauna XPE — {nombre}")
    L.append("")
    L.append(f"- Fecha de auditoría: {date.today():%d-%m-%Y}")
    L.append(f"- Archivo: `{libro.ruta_original.name}` (formato real: **{libro.formato}**)")
    if libro.ruta != libro.ruta_original:
        L.append(f"- El archivo se convirtió a .xlsx con LibreOffice antes de auditarlo ({libro.formato}).")
    hojas = ", ".join(f"{t.hoja} ({len(t.filas_datos)} registros)" for t in libro.registros)
    L.append(f"- Hojas auditadas: Estaciones ({len(libro.estaciones.filas_datos)} estaciones), {hojas}")
    for h in libro.registros_sin_datos:
        L.append(f"- Hoja `{h}`: sin datos, no se auditó.")
    for a in libro.avisos:
        L.append(f"- ⚠️ {a}")
    L.append("")

    L.append("## Puntaje de calidad")
    L.append("")
    etiqueta_despues = "Proyectado tras auto-correcciones" if solo_reporte else "Después (archivo _CORREGIDA)"
    L.append(f"| Indicador | Antes | {etiqueta_despues} |")
    L.append("|---|---|---|")
    L.append(f"| **Estado** | **{antes.estado}** | **{despues.estado}** |")
    L.append(f"| Índice de calidad global (0–100) | {antes.indice:.1f} | {despues.indice:.1f} |")
    L.append(f"| % registros limpios | {antes.pct_registros:.1f}% ({antes.n_registros_limpios}/{antes.n_registros}) | "
             f"{despues.pct_registros:.1f}% ({despues.n_registros_limpios}/{despues.n_registros}) |")
    L.append(f"| % estaciones limpias | {antes.pct_estaciones:.1f}% ({antes.n_estaciones_limpias}/{antes.n_estaciones}) | "
             f"{despues.pct_estaciones:.1f}% ({despues.n_estaciones_limpias}/{despues.n_estaciones}) |")
    L.append(f"| Reglas 🔴 distintas | {', '.join(antes.reglas_criticas) or '—'} | {', '.join(despues.reglas_criticas) or '—'} |")
    for s in Severidad:
        L.append(f"| Hallazgos {s.etiqueta} | {antes.por_severidad[s.etiqueta]} | {despues.por_severidad[s.etiqueta]} |")
    L.append("")
    L.append(f"**Fórmula:** {formula_puntaje(config)}")
    n_ok = sum(1 for _, d in decisiones.values() if d.tipo == "ok")
    if n_ok:
        L.append("")
        L.append(f"En «después» no restan los {n_ok} hallazgos que marcaste como «está bien así».")
    L.append("")
    L.append("**Estado:** ✅ LISTA PARA ANÁLISIS = sin 🔴 ni 🟠 · ⚠️ USABLE CON REVISIÓN = sin 🔴 pero con 🟠 · ❌ NO USAR = hay 🔴.")
    L.append("")

    L.append(f"## Qué se corrigió solo ({len(automaticos)} celdas)")
    L.append("")
    if solo_reporte:
        L.append("Modo `--solo-reporte`: no se escribió el archivo corregido. Estas son las correcciones que se aplicarían:")
        L.append("")
    L += _resumen_cambios(automaticos) or ["- Nada."]
    for a in avisos_cambios:
        L.append(f"- ⚠️ {a}")
    if cambios and not solo_reporte:
        L.append("")
        L.append("Detalle celda a celda en `cambios_aplicados.csv`.")
    L.append("")

    if decisiones:
        L.append(f"## Decisiones tomadas en la revisión ({len(decisiones)})")
        L.append("")
        for h, d in sorted(decisiones.values(), key=lambda x: (_orden_natural(x[0].id_estacion or "~"), x[0].id_regla)):
            donde = f"`{h.hoja}!{h.celda}`" if h.celda else f"`{h.hoja}`"
            est = f" ({h.id_estacion})" if h.id_estacion else ""
            L.append(f"- {h.severidad.emoji} **{h.id_regla}** {donde}{est} — {_md(h.problema)} → **{_md(d.describir())}**")
        L.append("")

    aceptados = {k for k, (_, d) in decisiones.items() if d.tipo == "ok"}
    pendientes = [h for h in hallazgos_despues if h.clave() not in aceptados]
    graves = [h for h in pendientes if h.severidad is not Severidad.INFO]
    L.append(f"## Qué falta decidir ({len(graves)} 🔴/🟠 y {len(pendientes) - len(graves)} 🟡 pendientes)")
    L.append("")
    L.append("Agrupado por estación. Escribe tu decisión en la columna «Decisión / Corrección» de la hoja HALLAZGOS del archivo _REVISION.")
    L.append("")
    por_est = defaultdict(list)
    sin_est = []
    for h in pendientes:
        (por_est[h.id_estacion] if h.id_estacion else sin_est).append(h)
    ambientes = {
        str(libro.estaciones.valor(f, "id")).strip(): libro.estaciones.valor(f, "ambiente")
        for f in libro.estaciones.filas_datos
    }
    for est in sorted(por_est, key=_orden_natural):
        hs = por_est[est]
        conteo = Counter(h.severidad.emoji for h in hs)
        resumen = " ".join(f"{e}{n}" for e, n in sorted(conteo.items(), key=lambda kv: "🔴🟠🟡".index(kv[0])))
        amb = ambientes.get(est)
        L.append(f"### {est}{f' — {amb}' if amb else ''} ({resumen})")
        L.append("")
        repetidas = Counter(h.id_regla for h in hs if h.severidad is Severidad.INFO)
        resumidas = set()
        for h in hs:
            if h.severidad is Severidad.INFO and repetidas[h.id_regla] > 4:
                if h.id_regla not in resumidas:
                    resumidas.add(h.id_regla)
                    celdas = [x.celda for x in hs if x.id_regla == h.id_regla]
                    L.append(f"- 🟡 **{h.id_regla}** {REGLAS[h.id_regla].titulo}: {len(celdas)} celdas ({', '.join(celdas[:8])}…). Ej.: {_md(h.problema)}")
                continue
            L.append(_linea_hallazgo(h))
        L.append("")
    if sin_est:
        L.append("### Generales (sin estación: plantilla, diccionario, indicadores)")
        L.append("")
        por_regla = defaultdict(list)
        for h in sin_est:
            por_regla[h.id_regla].append(h)
        for r, hs in por_regla.items():
            if len(hs) > 4:
                celdas = ", ".join(f"{h.hoja}!{h.celda}" for h in hs[:8] if h.celda)
                L.append(f"- {hs[0].severidad.emoji} **{r}** {REGLAS[r].titulo if r in REGLAS else ''}: "
                         f"{len(hs)} hallazgos ({celdas} …). Ej.: {_md(hs[0].problema)}")
            else:
                L += [_linea_hallazgo(h) for h in hs]
        L.append("")

    L.append("## Indicadores")
    L.append("")
    for hoja, st in estadisticas.get("R11", {}).items():
        L.append(f"- **R11** ({hoja}): {st['pct']:.0f}% de los registros de aves en transecto ({st['n_en_rango']} de {st['n']}) "
                 f"fue entre {st['rango']}; {len(st['estaciones_mayoria'])} de {st['n_estaciones']} estaciones tienen la mayoría de sus registros de aves en ese horario.")
    sin_registros = [h.id_estacion for h in hallazgos_despues if h.id_regla == "E12"]
    if sin_registros:
        L.append(f"- Estaciones sin registros (cuentan como 0, no se borran): {', '.join(sorted(sin_registros, key=_orden_natural))}.")
    L.append("")
    L += _lineas_origen(origen or {}, config)

    if verificacion is not None:
        v = verificacion
        L.append("## Verificación del archivo corregido")
        L.append("")
        L.append(f"- {_check(v.x14_ok)} Listas desplegables (`x14:dataValidation`): original {sum(v.x14_original.values())}, "
                 f"corregida {sum(v.x14_corregida.values())} ({', '.join(f'{k}: {n}' for k, n in v.x14_corregida.items() if n)}).")
        L.append(f"- {_check(v.formulas_ok)} Fórmulas: original {sum(v.formulas_original.values())}, corregida {sum(v.formulas_corregida.values())}.")
        L.append(f"- {_check(v.estilos_identicos)} `xl/styles.xml` idéntico al original.")
        L.append(f"- {_check(v.recalculo_al_abrir)} `fullCalcOnLoad=\"1\"` en `<calcPr>`: Excel recalcula todo al abrir.")
        L.append(f"- {_check(v.celdas_ok)} Celdas que cambiaron: {len(v.celdas_distintas)} (esperadas {len(v.celdas_esperadas)}); ninguna otra celda se tocó.")
        if v.libreoffice is not None:
            lo = v.libreoffice
            L.append(f"- {_check(not lo['errores_nuevos'])} Recalculado con LibreOffice headless: celdas con error en fórmulas "
                     f"original {lo['errores_original']}, corregida {lo['errores_corregida']}, errores nuevos {len(lo['errores_nuevos'])}"
                     + (f" ({', '.join(lo['errores_nuevos'][:10])})" if lo["errores_nuevos"] else "")
                     + (f"; resueltos {len(lo['errores_resueltos'])}" if lo["errores_resueltos"] else "") + ".")
            if lo.get("diferencias_uniones") is not None:
                L.append(f"- {_check(not lo['diferencias_uniones'])} Uniones rehechas en código vs. fórmulas recalculadas por LibreOffice: "
                         f"{len(lo['diferencias_uniones'])} diferencias.")
        else:
            L.append("- — LibreOffice no está disponible: no se verificó el recálculo.")
        if v.reauditoria_coincide is not None:
            L.append(f"- {_check(v.reauditoria_coincide)} Volver a auditar _CORREGIDA da los mismos hallazgos que la auditoría en memoria"
                     + (f" (diferencias: {'; '.join(v.diferencias_reauditoria[:5])})" if v.diferencias_reauditoria else "") + ".")
        L.append("")

    L.append("## Archivos generados")
    L.append("")
    L += [f"- `{a}`" for a in archivos]
    L.append("")

    L.append("## Mapeo de columnas detectado")
    L.append("")
    L.append("| Hoja | Fila enc. | Clave | Columna | Encabezado | Fórmula |")
    L.append("|---|---|---|---|---|---|")
    for m in tabla_mapeo(libro):
        L.append(f"| {m['hoja']} | {m['fila_encabezado']} | {m['clave']} | {m['columna']} | {_md(m['encabezado'])} | {'sí' if m['formula'] else ''} |")
    L.append("")
    L.append("Listas de valores permitidos:")
    L.append("")
    for k, v in libro.listas.items():
        L.append(f"- **{k}** ({libro.origen_listas.get(k, '')}): {', '.join(v)}")
    L.append(f"- **Directo/Indirecto** ({libro.origen_directo_indirecto}): "
             + "; ".join(f"{k}: {', '.join(v)}" for k, v in libro.directo_indirecto.items()))
    L.append("")

    L.append("## Criterios y supuestos")
    L.append("")
    L += [
        "- Las uniones Registros↔Estaciones↔nombres BD se rehacen en código y **no** se usan los valores cacheados de las fórmulas. "
        "Se une como R/pandas (exacto); la unión que ignora mayúsculas/espacios solo se usa para diagnosticar y sugerir.",
        "- Una fila de Registros es un registro solo si tiene `ID Estación`; si tiene datos sin ID se informa (R24).",
        "- E04 solo se corrige solo si `ID Final Estación` está vacío o difiere en mayúsculas/espacios; si es otro código, puede ser un renombre deliberado.",
        "- E11: «Si/SI/x» → 1, «No» → 0, vacío → 0.",
        "- E13 solo corrige si hay un valor mayoritario y la variante es un prefijo o un typo claro (similitud ≥ 0,85).",
        f"- R07: atípica si supera Q3 + {config['abundancia']['factor_iqr']}×max(IQR, 1) dentro de la especie "
        f"(mín. {config['abundancia']['min_registros_especie']} registros) o el p{config['abundancia']['percentil']} con ≥ {config['abundancia']['min_registros_percentil']} registros.",
        f"- R09: el «grueso» de la campaña es el rango p5–p95 de las fechas; tolerancia {config['fechas']['tolerancia_dias']} días.",
        "- R15 compara estación, especie, fecha, hora y abundancia (requiere fecha y hora); se marca la repetición, no la primera fila.",
        "- Reglas adicionales a la lista original: E14, E15, R21–R24, B01, T01, X01, X02 (ver descripción en la hoja RESUMEN).",
        "- `base_limpia_larga.csv` no borra filas: `excluir_motivo` dice por qué una fila no debe entrar al análisis y "
        "`usar_en_abundancia` = «no» marca evidencia indirecta o abundancia no válida (entra a riqueza, no a abundancia).",
    ]
    L.append("")
    ruta.write_text("\n".join(L), encoding="utf-8")
