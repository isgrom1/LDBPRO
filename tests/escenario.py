"""Escenario de aceptación: la planilla real + los errores de la revisión manual que no
están en esta versión del archivo (PMF20, PMF29, PMF32, PMF35, Cururo 'P', ID Final vacío…).

Se inyectan con el mismo editor XML que escribe _CORREGIDA, así que además sirve de prueba
de ese editor.
"""

from __future__ import annotations

from pathlib import Path

from auditor import xlsx_xml

# Fechas y horas como las guarda Excel (número de serie y fracción del día)
FECHA_29_08_2026 = 46263
HORA_10_15 = 10.25 / 24

NUEVAS_ESTACIONES = {
    # fila: (ID, (E, S) estación, (E, S) inicio, (E, S) fin)
    25: ("PMF29", (250800, 6539300), (250645, 6539096), (250950, 6539500)),  # fin = fin de PMF32, ~506 m
    26: ("PMF32", (250900, 6539400), (250850, 6539300), (250950, 6539500)),  # ~224 m
    27: ("PMF35", (251500, 6539300), (251490, 6539290), (251515, 6539316)),  # ~36 m
}


def cambios_escenario() -> dict[str, dict[str, object]]:
    est: dict[str, object] = {}
    for fila in range(3, 25):
        est[f"I{fila}"] = None  # ID Final Estación vacío en todas
        # Proyecto: la mayoría "Punta del Sol"; las últimas quedan "Punta del So" (truncado)
        est[f"B{fila}"] = "Punta del Sol" if fila < 19 else "Punta del So"
    for fila, (id_, p, ini, fin) in NUEVAS_ESTACIONES.items():
        est.update({
            f"A{fila}": fila - 2, f"B{fila}": "Punta del Sol", f"C{fila}": "Coquimbo", f"D{fila}": "Invierno",
            f"F{fila}": "Matorral bajo", f"G{fila}": "Plano", f"H{fila}": id_, f"I{fila}": None,
            f"K{fila}": p[0], f"L{fila}": p[1], f"M{fila}": ini[0], f"N{fila}": ini[1],
            f"O{fila}": fin[0], f"P{fila}": fin[1], f"Q{fila}": 1, f"R{fila}": 0, f"S{fila}": 0,
            f"T{fila}": 0, f"U{fila}": 0,
        })

    reg: dict[str, object] = {"AI196": "P"}  # Cururo de PMF18 con abundancia "P"
    nuevos = [
        # fila, estación, especie, tipo, abundancia
        (208, "PMF20", "Chincol", "Vocalización", 1),
        (209, "PMF29", "Cururo", "Vocalización", "P"),
        (210, "PMF32", "Loica", "Individuo", 2),
    ]
    for fila, id_, especie, tipo, ab in nuevos:
        reg.update({
            f"A{fila}": fila - 9, f"D{fila}": FECHA_29_08_2026, f"K{fila}": "Despejado", f"L{fila}": id_,
            f"Z{fila}": HORA_10_15, f"AA{fila}": "Transecto", f"AC{fila}": tipo, f"AD{fila}": especie,
            f"AF{fila}": "Indeterminado", f"AG{fila}": "Vivo", f"AH{fila}": "Indeterminado", f"AI{fila}": ab,
        })

    ta = {"L10": "PMF99", "D10": FECHA_29_08_2026, "Q10": HORA_10_15, "R10": "Chincol", "V10": 1}
    return {"Estaciones": est, "Registros": reg, "Registros TA": ta}


def crear_escenario(original: Path, destino: Path) -> Path:
    xlsx_xml.escribir_con_cambios(original, destino, cambios_escenario())
    return destino
