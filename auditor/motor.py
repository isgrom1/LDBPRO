"""Ejecución de las reglas, auto-correcciones en memoria y puntaje de calidad."""

from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass, field

from . import reglas  # noqa: F401  (registra las reglas)
from .contexto import Contexto
from .hallazgos import REGLAS, Hallazgo, Severidad
from .modelo import Libro

ID_ERROR = "ERROR"


def _orden(h: Hallazgo):
    r = REGLAS.get(h.id_regla)
    return (-h.severidad.nivel, r.orden if r else 999, h.hoja, h.fila or 0, h.celda or "")


def auditar(libro: Libro, config: dict, incluir_cache: bool = True) -> tuple[list[Hallazgo], Contexto]:
    ctx = Contexto(libro, config)
    hallazgos: list[Hallazgo] = []
    for r in sorted(REGLAS.values(), key=lambda r: r.orden):
        if r.usa_cache and not incluir_cache:
            continue
        try:
            hallazgos.extend(r.funcion(ctx) or [])
        except Exception as exc:  # una regla rota no debe tumbar la auditoría completa
            traceback.print_exc(file=sys.stderr)
            hallazgos.append(
                Hallazgo(ID_ERROR, libro.estaciones.hoja, None, None, None, None,
                         f"La regla {r.id} falló: {exc!r}", "Revisa el código de la regla.",
                         Severidad.ADVERTENCIA)
            )
    return sorted(hallazgos, key=_orden), ctx


# --------------------------------------------------------------------------- correcciones


@dataclass
class Cambio:
    hoja: str
    celda: str
    fila: int
    clave: str
    valor_anterior: object
    valor_nuevo: object
    reglas: list[str] = field(default_factory=list)
    reemplaza_formula: bool = False


def consolidar_cambios(libro: Libro, hallazgos: list[Hallazgo]) -> tuple[list[Cambio], list[str]]:
    """Un cambio por celda. Si dos reglas proponen valores distintos, no se aplica ninguno."""
    cambios: dict[tuple[str, str], Cambio] = {}
    conflictos: dict[tuple[str, str], set] = {}
    for h in hallazgos:
        c = h.correccion
        if c is None:
            continue
        llave = (c.hoja, c.celda)
        if llave in conflictos:
            conflictos[llave].add((h.id_regla, repr(c.valor_nuevo)))
            continue
        if llave in cambios:
            previo = cambios[llave]
            if previo.valor_nuevo == c.valor_nuevo and type(previo.valor_nuevo) is type(c.valor_nuevo):
                if h.id_regla not in previo.reglas:
                    previo.reglas.append(h.id_regla)
            else:
                conflictos[llave] = {(r, repr(previo.valor_nuevo)) for r in previo.reglas}
                conflictos[llave].add((h.id_regla, repr(c.valor_nuevo)))
                del cambios[llave]
            continue
        if c.clave.startswith(Libro.PREFIJO_LISTA):  # celda de una lista de 'validación datos'
            anterior = libro.valor_lista(c.hoja, c.celda)
            cambios[llave] = Cambio(c.hoja, c.celda, c.fila, c.clave, anterior, c.valor_nuevo, [h.id_regla])
            continue
        t = libro.tabla(c.hoja)
        if t.es_formula.at[c.fila, c.clave]:
            conflictos[llave] = {(h.id_regla, "la celda tiene fórmula")}
            continue
        cambios[llave] = Cambio(c.hoja, c.celda, c.fila, c.clave, t.valor(c.fila, c.clave), c.valor_nuevo, [h.id_regla])
    avisos = [
        f"{hoja}!{celda}: correcciones contradictorias ({'; '.join(f'{r} → {v}' for r, v in sorted(props))}); no se aplicó ninguna."
        for (hoja, celda), props in conflictos.items()
    ]
    return list(cambios.values()), avisos


def aplicar_en_memoria(libro: Libro, cambios: list[Cambio]) -> Libro:
    nuevo = libro.copia()
    for c in cambios:
        if c.clave.startswith(Libro.PREFIJO_LISTA):
            nuevo.fijar_lista(c.hoja, c.celda, c.valor_nuevo)
            continue
        t = nuevo.tabla(c.hoja)
        t.valores.at[c.fila, c.clave] = c.valor_nuevo
        t.es_formula.at[c.fila, c.clave] = False  # un valor escrito a mano reemplaza la fórmula
        t.formulas.at[c.fila, c.clave] = None
    return nuevo


def cambios_por_hoja(cambios: list[Cambio]) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for c in cambios:
        out.setdefault(c.hoja, {})[c.celda] = c.valor_nuevo
    return out


# --------------------------------------------------------------------------- puntaje

ESTADO_LISTA = "✅ LISTA PARA ANÁLISIS"
ESTADO_REVISION = "⚠️ USABLE CON REVISIÓN"
ESTADO_NO_USAR = "❌ NO USAR"


@dataclass
class Puntaje:
    n_registros: int
    n_registros_limpios: int
    n_estaciones: int
    n_estaciones_limpias: int
    pct_registros: float
    pct_estaciones: float
    reglas_criticas: list[str]
    indice: float
    estado: str
    por_severidad: dict[str, int]


def calcular_puntaje(libro: Libro, hallazgos: list[Hallazgo], config: dict) -> Puntaje:
    cfg = config["puntaje"]
    registros = {(t.hoja, f) for t in libro.registros for f in t.filas_datos}
    estaciones = {(libro.estaciones.hoja, f) for f in libro.estaciones.filas_datos}
    graves = [h for h in hallazgos if h.severidad in (Severidad.CRITICO, Severidad.ADVERTENCIA)]
    filas_graves = {(h.hoja, h.fila) for h in graves if h.fila is not None}
    reg_malos = filas_graves & registros
    est_malas = filas_graves & estaciones
    pct_reg = 100 * (len(registros) - len(reg_malos)) / len(registros) if registros else 100.0
    pct_est = 100 * (len(estaciones) - len(est_malas)) / len(estaciones) if estaciones else 100.0
    criticas = sorted({h.id_regla for h in hallazgos if h.severidad is Severidad.CRITICO})
    indice = cfg["peso_registros"] * pct_reg + cfg["peso_estaciones"] * pct_est
    indice -= cfg["penalizacion_por_regla_critica"] * len(criticas)
    indice = max(0.0, min(100.0, indice))
    hay_criticos = bool(criticas)
    hay_adv = any(h.severidad is Severidad.ADVERTENCIA for h in hallazgos)
    estado = ESTADO_NO_USAR if hay_criticos else ESTADO_REVISION if hay_adv else ESTADO_LISTA
    por_sev = {s.etiqueta: sum(1 for h in hallazgos if h.severidad is s) for s in Severidad}
    return Puntaje(
        len(registros), len(registros) - len(reg_malos), len(estaciones), len(estaciones) - len(est_malas),
        pct_reg, pct_est, criticas, indice, estado, por_sev,
    )


def formula_puntaje(config: dict) -> str:
    c = config["puntaje"]
    return (
        f"Índice = {c['peso_registros']:.0%} × %registros limpios + {c['peso_estaciones']:.0%} × "
        f"%estaciones limpias − {c['penalizacion_por_regla_critica']} × (n.º de reglas 🔴 distintas), "
        "acotado a 0–100. Un registro o estación es «limpio» si no tiene hallazgos 🔴 ni 🟠 en su fila."
    )
