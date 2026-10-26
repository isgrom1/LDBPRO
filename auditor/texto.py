"""Normalización de textos, números, fechas y horas tal como llegan desde la planilla."""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import date, datetime, time, timedelta

# Caracteres que se ven como espacio (o no se ven) y rompen uniones y conversiones.
INVISIBLES = {
    " ": "espacio de no separación (U+00A0)",
    " ": "espacio de cifra (U+2007)",
    " ": "espacio fino de no separación (U+202F)",
    "​": "espacio de ancho cero (U+200B)",
    "﻿": "marca BOM (U+FEFF)",
    "\t": "tabulación",
}

_ESPACIOS_RE = re.compile(r" {2,}")
_NUM_RE = re.compile(r"^[+-]?\d+(?:[.,]\d+)?$")
_MILES_RE = re.compile(r"^[+-]?\d{1,3}(?:\.\d{3})+$")
_HORA_RE = re.compile(r"^(\d{1,2})\s*[:.hH]\s*(\d{2})(?:\s*[:.]\s*(\d{2}))?\s*(?:hrs?|h)?$")
_EXCEL_EPOCH = datetime(1899, 12, 30)


def limpiar(v) -> str:
    """Texto sin caracteres invisibles, sin espacios dobles ni en los extremos."""
    if v is None:
        return ""
    s = str(v)
    for ch in INVISIBLES:
        s = s.replace(ch, " ")
    s = s.replace("\r", " ").replace("\n", " ")
    return _ESPACIOS_RE.sub(" ", s).strip()


def clave(v) -> str:
    """Clave de comparación que ignora mayúsculas y espacios (lo que hace Excel, casi)."""
    return limpiar(v).casefold()


# Abundancia «P»: especie presente sin conteo de individuos (convención de las tablas de la LdB de XPE)
PRESENCIA = "P"
_PRESENCIA = {"p", "p.", "presencia", "presente", "pres", "pres."}


def es_presencia(v) -> bool:
    return isinstance(v, str) and clave(v) in _PRESENCIA


def sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def clave_flexible(v) -> str:
    """Además ignora tildes. Solo para agrupar por similitud, nunca para unir."""
    return sin_tildes(clave(v))


def tokens(v) -> list[str]:
    return re.findall(r"[a-z0-9]+", clave_flexible(v))


def es_vacio(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    return isinstance(v, str) and limpiar(v) == ""


def describir_espacios(v) -> list[str]:
    """Lista legible de los problemas de espacios de un texto (vacía si no hay)."""
    if not isinstance(v, str):
        return []
    problemas = []
    for ch, nombre in INVISIBLES.items():
        if ch in v:
            if v.startswith(ch):
                pos = "al inicio"
            elif v.endswith(ch):
                pos = "al final"
            else:
                pos = "en medio"
            problemas.append(f"{nombre} {pos}")
    if "  " in v:
        problemas.append("espacios dobles")
    if v.startswith(" "):
        problemas.append("espacio al inicio")
    if v.endswith(" "):
        problemas.append("espacio al final")
    if "\n" in v or "\r" in v:
        problemas.append("salto de línea")
    return problemas


def mostrar(v) -> str:
    """Representación visible de un valor: deja a la vista los caracteres invisibles."""
    if v is None:
        return "(vacío)"
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M") if v.time() != time(0) else v.strftime("%Y-%m-%d")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, time):
        return v.strftime("%H:%M")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    s = str(v)
    for ch in INVISIBLES:
        s = s.replace(ch, f"[U+{ord(ch):04X}]")
    if s != s.strip(" "):
        s = f"«{s}»"
    return s


def a_numero(v) -> float | None:
    """Convierte a número aceptando textos con espacios invisibles. None si no se puede."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return None if isinstance(v, float) and math.isnan(v) else float(v)
    if isinstance(v, str):
        s = limpiar(v).replace(" ", "")
        if _MILES_RE.match(s):
            s = s.replace(".", "")
        elif _NUM_RE.match(s):
            s = s.replace(",", ".")
        else:
            return None
        return float(s)
    return None


def es_numero_real(v) -> bool:
    """True si la celda guarda un número de verdad (no un texto con forma de número)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and not (
        isinstance(v, float) and math.isnan(v)
    )


def numero_para_celda(x: float):
    """int si es entero (para no escribir 251835.0 en la planilla)."""
    return int(x) if float(x).is_integer() else float(x)


def a_fecha(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if es_numero_real(v) and 20000 < v < 80000:
        return (_EXCEL_EPOCH + timedelta(days=float(v))).date()
    if isinstance(v, str):
        s = limpiar(v)
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y", "%d.%m.%Y"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                pass
    return None


def a_hora(v) -> time | None:
    if isinstance(v, datetime):
        return v.time()
    if isinstance(v, time):
        return v
    if isinstance(v, timedelta):
        seg = int(v.total_seconds()) % 86400
        return time(seg // 3600, (seg % 3600) // 60, seg % 60)
    if es_numero_real(v) and 0 <= v < 1:
        seg = round(float(v) * 86400) % 86400
        return time(seg // 3600, (seg % 3600) // 60, seg % 60)
    if isinstance(v, str):
        m = _HORA_RE.match(limpiar(v))
        if m:
            h, mi, se = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
            if h < 24 and mi < 60 and se < 60:
                return time(h, mi, se)
    return None


def fecha_a_excel(d: date) -> int:
    """Número de serie con que Excel guarda una fecha."""
    return (d - _EXCEL_EPOCH.date()).days


def hora_a_excel(h: time) -> float:
    """Fracción del día con que Excel guarda una hora."""
    return (h.hour * 3600 + h.minute * 60 + h.second) / 86400


def hora_desde_texto(s: str) -> time:
    """'12:00' -> time(12, 0). Para leer horarios desde config.yaml."""
    h = a_hora(s)
    if h is None:
        raise ValueError(f"Hora inválida en la configuración: {s!r}")
    return h


def en_rango_horario(h: time, inicio: time, fin: time) -> bool:
    """Incluye ambos extremos; soporta rangos que cruzan la medianoche (19:00-06:00)."""
    if inicio <= fin:
        return inicio <= h <= fin
    return h >= inicio or h <= fin
