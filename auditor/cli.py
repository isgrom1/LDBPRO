"""Orquestación: auditar, corregir, verificar y escribir todas las salidas."""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import date
from pathlib import Path

from .config import cargar_config
from .formato import nombre_base
from .modelo import Libro, tabla_mapeo
from .motor import Puntaje
from .sesion import Resultado, analizar, generar

__all__ = ["Resultado", "carpeta_salida", "ejecutar", "main"]


def carpeta_salida(base: Path, nombre: str, hoy: date) -> Path:
    """auditoria_<nombre>_<fecha>/; nunca reutiliza una existente (podría tener decisiones anotadas)."""
    candidata = base / f"auditoria_{nombre}_{hoy:%Y-%m-%d}"
    n = 2
    while candidata.exists():
        candidata = base / f"auditoria_{nombre}_{hoy:%Y-%m-%d}_{n}"
        n += 1
    candidata.mkdir(parents=True)
    return candidata


def ejecutar(
    planilla: str | Path, config: str | Path | dict | None = None, solo_reporte: bool = False,
    salida: str | Path | None = None, con_libreoffice: bool = True, hoy: date | None = None,
    informar=print,
) -> Resultado:
    """Audita, aplica las auto-correcciones y escribe todas las salidas (sin decisiones manuales)."""
    ruta = Path(planilla).resolve()
    if not ruta.is_file():
        raise FileNotFoundError(f"No existe el archivo {ruta}")
    cfg = config if isinstance(config, dict) else cargar_config(config)
    carpeta = carpeta_salida(Path(salida).resolve() if salida else ruta.parent, nombre_base(ruta), hoy or date.today())
    with tempfile.TemporaryDirectory(prefix="auditor_xpe_") as tmp:
        analisis = analizar(ruta, cfg, tmp, informar)
        informar(texto_mapeo(analisis.libro))
        return generar(analisis, carpeta, solo_reporte=solo_reporte, con_libreoffice=con_libreoffice,
                       informar=informar)


def texto_mapeo(libro: Libro) -> str:
    lineas = ["Mapeo de columnas detectado:"]
    actual = None
    for m in tabla_mapeo(libro):
        if m["hoja"] != actual:
            actual = m["hoja"]
            lineas.append(f"  [{actual}] encabezados en la fila {m['fila_encabezado']}")
        marca = " (fórmula)" if m["formula"] else ""
        lineas.append(f"    {m['columna']:>3}  {m['clave']:<18} {m['encabezado'][:60]}{marca}")
    for t in libro.registros_sin_datos:
        lineas.append(f"  [{t}] sin datos: no se audita")
    for a in libro.avisos:
        lineas.append(f"  AVISO: {a}")
    return "\n".join(lineas)


def _linea_puntaje(etiqueta: str, p: Puntaje) -> str:
    return (f"{etiqueta:<9} {p.estado:<24} índice {p.indice:5.1f} | registros limpios {p.pct_registros:5.1f}% "
            f"({p.n_registros_limpios}/{p.n_registros}) | estaciones limpias {p.pct_estaciones:5.1f}% "
            f"({p.n_estaciones_limpias}/{p.n_estaciones}) | reglas 🔴: {', '.join(p.reglas_criticas) or '—'}")


def main(argv=None) -> int:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(errors="replace")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(
        prog="auditar_planilla.py",
        description="Auditor de planillas de fauna XPE (Formato_Planilla_Fauna_XPE_Externo_v4).",
    )
    ap.add_argument("planilla", help="Ruta de la planilla (.xlsx, .xlsb o .xlsb.xlsx)")
    ap.add_argument("--config", help="Archivo YAML de configuración (por defecto, config.yaml del proyecto)")
    ap.add_argument("--solo-reporte", action="store_true",
                    help="Audita y reporta, sin escribir el archivo corregido")
    ap.add_argument("--salida", help="Carpeta donde crear auditoria_<nombre>_<fecha>/ (por defecto, junto a la planilla)")
    ap.add_argument("--sin-libreoffice", action="store_true",
                    help="No verificar el recálculo del archivo corregido con LibreOffice")
    args = ap.parse_args(argv)
    try:
        r = ejecutar(args.planilla, args.config, args.solo_reporte, args.salida, not args.sin_libreoffice)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print()
    print(_linea_puntaje("ANTES", r.puntaje_antes))
    print(_linea_puntaje("PROYECTADO" if args.solo_reporte else "DESPUÉS", r.puntaje_despues))
    print(f"Auto-correcciones {'propuestas' if args.solo_reporte else 'aplicadas'}: {len(r.cambios)} celdas")
    graves = sum(1 for h in r.hallazgos_despues if h.severidad.nivel >= 2)
    print(f"Pendientes de decisión: {graves} 🔴/🟠 y {len(r.hallazgos_despues) - graves} 🟡")
    if r.verificacion is not None:
        print(f"Verificación de _CORREGIDA: {'OK' if r.verificacion.todo_ok else 'CON PROBLEMAS (ver reporte.md)'}")
    print(f"Salidas en: {r.carpeta}")
    return 0
