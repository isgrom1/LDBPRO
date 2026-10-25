"""Hallazgos, severidades y registro de reglas.

Agregar una regla nueva = escribir una función que recibe el Contexto (con los
DataFrames de la planilla) y devuelve hallazgos, decorada con @regla::

    @regla("R99", "Descripción corta", Severidad.ADVERTENCIA)
    def r99(ctx):
        for t, fila, r in ctx.iter_registros():
            if ...:
                yield ctx.hallazgo("R99", t, fila, "abundancia", "Problema", "Sugerencia")

y ubicarla en un módulo importado por auditor/reglas/__init__.py.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum


class Severidad(Enum):
    CRITICO = (3, "🔴 CRÍTICO", "FFFF7C80")
    ADVERTENCIA = (2, "🟠 ADVERTENCIA", "FFFFC000")
    INFO = (1, "🟡 INFO", "FFFFFF66")

    @property
    def nivel(self) -> int:
        return self.value[0]

    @property
    def etiqueta(self) -> str:
        return self.value[1]

    @property
    def color(self) -> str:
        return self.value[2]

    @property
    def emoji(self) -> str:
        return self.value[1].split()[0]


@dataclass
class Correccion:
    hoja: str
    celda: str
    fila: int
    clave: str
    valor_nuevo: object


@dataclass
class Accion:
    """Corrección sugerida que el usuario puede aplicar con un clic.

    Por defecto escribe en la celda del hallazgo; hoja/celda permiten apuntar a otra
    (p. ej. marcar la metodología en Estaciones desde un registro).
    """

    etiqueta: str
    valor: object
    hoja: str | None = None
    celda: str | None = None


@dataclass
class Hallazgo:
    id_regla: str
    hoja: str
    celda: str | None
    fila: int | None
    id_estacion: str | None
    valor_actual: object
    problema: str
    sugerencia: str
    severidad: Severidad
    correccion: Correccion | None = None
    # Celdas adicionales a colorear en el archivo de revisión (p. ej. la otra estación de un duplicado)
    celdas_extra: list[str] = field(default_factory=list)
    acciones: list[Accion] = field(default_factory=list)
    # Valores permitidos para la celda (listas de 'validación datos'), para elegir de una lista
    opciones: list[str] | None = None

    @property
    def auto_corregible(self) -> bool:
        return self.correccion is not None

    def firma(self) -> tuple:
        """Identidad del hallazgo para comparar auditorías (antes/después, memoria/archivo)."""
        return (self.id_regla, self.hoja, self.celda, self.problema)

    def clave(self) -> str:
        """Identidad estable de un hallazgo para guardar decisiones: no depende del texto,
        que puede cambiar al corregir otras celdas."""
        return "|".join((self.id_regla, self.hoja, self.celda or self.problema))


@dataclass
class Regla:
    id: str
    titulo: str
    severidad: Severidad
    auto_corregible: bool
    funcion: Callable[..., Iterable[Hallazgo]]
    # Reglas que comparan contra los valores cacheados de las fórmulas: no aplican a un
    # archivo recién corregido (se recalcula al abrirlo, fullCalcOnLoad="1").
    usa_cache: bool = False
    orden: int = 0


REGLAS: dict[str, Regla] = {}

# Orden de ejecución y de listado: Estaciones, Registros, diccionario (B), plantilla (T), fórmulas (X)
_PREFIJOS = "ERBTX"


def _orden(id: str) -> int:
    grupo = _PREFIJOS.index(id[0]) if id[:1] in _PREFIJOS else len(_PREFIJOS)
    numero = int("".join(c for c in id if c.isdigit()) or 0)
    return grupo * 1000 + numero


def regla(id: str, titulo: str, severidad: Severidad, auto_corregible: bool = False, usa_cache: bool = False):
    def decorador(fn):
        if id in REGLAS:
            raise ValueError(f"Regla duplicada: {id}")
        REGLAS[id] = Regla(id, titulo, severidad, auto_corregible, fn, usa_cache, _orden(id))
        return fn

    return decorador
