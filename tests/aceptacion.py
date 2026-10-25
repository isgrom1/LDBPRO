"""Prueba de aceptación: errores encontrados en la revisión manual vs. hallazgos del auditor.

Cada ítem revisa primero en los datos crudos si la situación existe en el archivo; si existe,
la regla tiene que haberla detectado. Así un «no está en este archivo» nunca esconde una regla
que no funciona.

Uso:  python tests/aceptacion.py [planilla.xlsx]   (imprime la tabla esperado vs. detectado)
"""

from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from functools import lru_cache  # noqa: E402

from auditor.config import cargar_config  # noqa: E402
from auditor.contexto import Contexto  # noqa: E402
from auditor.texto import a_numero, es_vacio, limpiar, mostrar  # noqa: E402

DETECTADO, PARCIAL, AUSENTE, FALLA = "✅ detectado", "⚠️ parcial", "➖ no está en el archivo", "❌ NO DETECTADO"


def _hs(res, regla, **filtros):
    out = []
    for h in res.hallazgos_antes:
        if h.id_regla != regla:
            continue
        if all(getattr(h, k) == v for k, v in filtros.items()):
            out.append(h)
    return out


def _registros(res, nombre=None, estacion=None):
    for t in res.libro.registros:
        for f in t.filas_datos:
            if nombre is not None and t.valor(f, "nombre_comun") != nombre:
                continue
            if estacion is not None and limpiar(t.valor(f, "id")) != estacion:
                continue
            yield t, f


def _estacion(res, id_):
    est = res.libro.estaciones
    return next((f for f in est.filas_datos if limpiar(est.valor(f, "id")) == id_), None)


def item_pmf20(res):
    regs = list(_registros(res, estacion="PMF20"))
    if not regs:
        return AUSENTE, "ningún registro usa PMF20"
    if _estacion(res, "PMF20") is not None:
        return AUSENTE, "PMF20 sí existe en Estaciones"
    ok = all(_hs(res, "R01", hoja=t.hoja, celda=t.celda(f, "id")) for t, f in regs)
    return (DETECTADO if ok else FALLA), ", ".join(f"{t.hoja}!{t.celda(f, 'id')}" for t, f in regs)


def item_rhinella(res):
    regs = list(_registros(res, nombre="Rhinella spp."))
    if not regs:
        return AUSENTE, ""
    ok = all(_hs(res, r, hoja=t.hoja, celda=t.celda(f, "nombre_comun")) for t, f in regs for r in ("R02", "R04"))
    return (DETECTADO if ok else FALLA), "R02 + R04 en " + ", ".join(t.celda(f, "nombre_comun") for t, f in regs)


def item_cururo(res):
    partes, estados = [], []
    for est in ("PMF18", "PMF29"):
        regs = list(_registros(res, nombre="Cururo", estacion=est))
        if not regs:
            partes.append(f"{est}: no hay Cururo")
            estados.append(AUSENTE)
            continue
        for t, f in regs:
            v = t.valor(f, "abundancia")
            invalida = es_vacio(v) or a_numero(v) is None
            if not invalida:
                partes.append(f"{est}: abundancia {mostrar(v)} (válida)")
                estados.append(AUSENTE)
                continue
            ok = bool(_hs(res, "R06", hoja=t.hoja, celda=t.celda(f, "abundancia")))
            partes.append(f"{est} {t.celda(f, 'abundancia')}: «{mostrar(v)}»")
            estados.append(FALLA if not ok else DETECTADO if v == "P" else PARCIAL)
    if FALLA in estados:
        return FALLA, "; ".join(partes)
    if all(e == DETECTADO for e in estados):
        return DETECTADO, "; ".join(partes)
    if all(e == AUSENTE for e in estados):
        return AUSENTE, "; ".join(partes)
    return PARCIAL, "; ".join(partes) + " — lo que está en el archivo se detecta"


def item_lemniscata(res):
    regs = list(_registros(res, nombre="lagartija lemniscata"))
    if not regs:
        return AUSENTE, ""
    ok = all(_hs(res, r, hoja=t.hoja, celda=t.celda(f, "nombre_comun")) for t, f in regs for r in ("R03", "R05"))
    return (DETECTADO if ok else FALLA), "R03 + R05 en " + ", ".join(t.celda(f, "nombre_comun") for t, f in regs)


def item_mayusculas(res):
    partes, ok_total, alguno = [], True, False
    for nombre in ("Cometocino de gay", "Lagarto de zapallar"):
        regs = list(_registros(res, nombre=nombre))
        if not regs:
            partes.append(f"'{nombre}': no está")
            continue
        alguno = True
        ok = all(_hs(res, "R03", hoja=t.hoja, celda=t.celda(f, "nombre_comun")) for t, f in regs)
        ok_total &= ok
        partes.append(f"'{nombre}' ×{len(regs)}")
    if not alguno:
        return AUSENTE, "; ".join(partes)
    return (DETECTADO if ok_total else FALLA), "R03 en " + "; ".join(partes)


def item_nbsp(res):
    est = res.libro.estaciones
    partes, ok_total, alguno = [], True, False
    for id_ in ("PMF38", "PMF40", "PMF55"):
        f = _estacion(res, id_)
        celdas = [] if f is None else [
            c for c in est.columnas if c.startswith("utm_") and " " in str(est.valor(f, c) or "")
        ]
        if not celdas:
            partes.append(f"{id_}: sin U+00A0")
            continue
        alguno = True
        ok = all(_hs(res, "E05", celda=est.celda(f, c)) for c in celdas)
        ok_total &= ok
        partes.append(f"{id_} {', '.join(est.celda(f, c) for c in celdas)}")
    if not alguno:
        return AUSENTE, "; ".join(partes)
    return (DETECTADO if ok_total else FALLA), "E05 en " + "; ".join(partes)


def item_pmf29(res):
    f29, f32 = _estacion(res, "PMF29"), _estacion(res, "PMF32")
    if f29 is None or f32 is None:
        return AUSENTE, "PMF29 y PMF32 no están en Estaciones"
    largo = _contexto(res).largo_transecto(f29)
    e08 = _hs(res, "E08", id_estacion="PMF29")
    e09 = [h for h in _hs(res, "E09", id_estacion="PMF29") if "PMF32" in h.problema and "fin" in h.problema]
    ok = bool(e08) and bool(e09) and largo is not None and 480 <= largo <= 530
    return (DETECTADO if ok else FALLA), f"E09 (fin = fin de PMF32) + E08 ({largo:.0f} m)" if largo else "sin largo"


def item_cortos(res):
    partes, ok_total, alguno = [], True, False
    for id_, esperado in (("PMF35", 36), ("PMF55", 72)):
        f = _estacion(res, id_)
        if f is None:
            partes.append(f"{id_}: no está en Estaciones")
            continue
        alguno = True
        hs = _hs(res, "E08", id_estacion=id_)
        ok = bool(hs) and abs(a_numero(str(hs[0].valor_actual).split()[0]) - esperado) <= 3
        ok_total &= ok
        partes.append(f"{id_}: {hs[0].valor_actual if hs else 'sin E08'}")
    estado = DETECTADO if ok_total and len([p for p in partes if 'no está' not in p]) == 2 else (
        FALLA if not ok_total else PARCIAL)
    if not alguno:
        estado = AUSENTE
    return estado, "E08 — " + "; ".join(partes)


def item_id_final(res):
    est = res.libro.estaciones
    vacias = [f for f in est.filas_datos if es_vacio(est.valor(f, "id_final"))]
    if not vacias:
        return AUSENTE, f"las {len(est.filas_datos)} estaciones tienen ID Final (= ID)"
    ok = all(_hs(res, "E04", celda=est.celda(f, "id_final")) for f in vacias)
    todas = len(vacias) == len(est.filas_datos)
    estado = FALLA if not ok else DETECTADO if todas else PARCIAL
    return estado, f"E04 en {len(vacias)} de {len(est.filas_datos)} estaciones (auto-corregible)"


def item_caballo(res):
    uniones = _contexto(res).uniones
    regs = [(t, f) for t, f in _registros(res, estacion="PMF17") if uniones[t.hoja].at[f, "especie"] == "Equus caballus"]
    if not regs:
        return AUSENTE, ""
    ok = all(_hs(res, "R08", hoja=t.hoja, celda=t.celda(f, "nombre_comun")) for t, f in regs)
    return (DETECTADO if ok else FALLA), "R08 en " + ", ".join(t.celda(f, "nombre_comun") for t, f in regs)


def item_aves(res):
    st = res.estadisticas.get("R11", {}).get("Registros")
    if not st:
        return AUSENTE, "sin registros de aves en transecto"
    if st["pct"] <= 50:
        return AUSENTE, f"solo {st['pct']:.0f}% entre {st['rango']}"
    ok = bool(_hs(res, "R11"))
    return (DETECTADO if ok else FALLA), f"R11: {st['pct']:.0f}% ({st['n_en_rango']}/{st['n']}) entre {st['rango']}"


_CONTEXTOS: dict[int, Contexto] = {}


@lru_cache(maxsize=1)
def _config() -> dict:
    return cargar_config()


def _contexto(res) -> Contexto:
    if id(res) not in _CONTEXTOS:
        _CONTEXTOS[id(res)] = Contexto(res.libro, _config())
    return _CONTEXTOS[id(res)]


ESPERADOS = [
    ("PMF20 está en Registros pero no existe en Estaciones", "R01", item_pmf20),
    ("Rhinella spp. no está en nombres BD y da #N/A", "R02/R04", item_rhinella),
    ("Cururo con abundancia «P» en PMF18 y PMF29", "R06", item_cururo),
    ("«lagartija lemniscata» en minúscula vs «Lagartija lemniscata»", "R03/R05", item_lemniscata),
    ("«Cometocino de gay» y «Lagarto de zapallar» difieren solo en mayúsculas", "R03", item_mayusculas),
    ("Coordenadas con U+00A0 en PMF38, PMF40 y PMF55", "E05", item_nbsp),
    ("PMF29: fin idéntico al de PMF32 y largo ~505 m", "E08+E09", item_pmf29),
    ("PMF35 con transecto de ~36 m y PMF55 con ~72 m", "E08", item_cortos),
    ("ID Final Estación vacío en todas las estaciones", "E04", item_id_final),
    ("Equus caballus en PMF17", "R08", item_caballo),
    ("La mayoría de los registros de aves son entre 12 y 17 h", "R11", item_aves),
]


def evaluar(res) -> list[tuple[str, str, str, str]]:
    return [(desc, reglas, *fn(res)) for desc, reglas, fn in ESPERADOS]


def tabla_markdown(real, escenario=None) -> str:
    filas = evaluar(real)
    esc = evaluar(escenario) if escenario is not None else None
    cab = "| # | Error esperado (revisión manual) | Regla | Planilla adjunta | Detalle |"
    sep = "|---|---|---|---|---|"
    if esc:
        cab += " Escenario con los casos inyectados |"
        sep += "---|"
    lineas = [cab, sep]
    for i, (desc, reglas, estado, detalle) in enumerate(filas):
        linea = f"| {i + 1} | {desc} | {reglas} | {estado} | {detalle} |"
        if esc:
            linea += f" {esc[i][2]} — {esc[i][3]} |"
        lineas.append(linea)
    return "\n".join(lineas)


if __name__ == "__main__":
    import tempfile

    from escenario import crear_escenario

    from auditor.cli import ejecutar

    planilla = Path(sys.argv[1]) if len(sys.argv) > 1 else next(Path(__file__).parent.joinpath("datos").glob("*.xlsx"))
    with tempfile.TemporaryDirectory() as tmp:
        r = ejecutar(planilla, salida=tmp, con_libreoffice=False, informar=lambda *a: None)
        esc = crear_escenario(planilla, Path(tmp) / "escenario.xlsx")
        re_ = ejecutar(esc, salida=tmp, con_libreoffice=False, informar=lambda *a: None)
        print(tabla_markdown(r, re_))
