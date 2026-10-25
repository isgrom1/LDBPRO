"""Orquestación: auditar, corregir, verificar y escribir todas las salidas."""

from __future__ import annotations

import argparse
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .config import cargar_config
from .formato import convertir_con_libreoffice, detectar_formato, nombre_base
from .hallazgos import Hallazgo
from .modelo import Libro, leer_libro, tabla_mapeo
from .motor import (
    Cambio,
    Puntaje,
    aplicar_en_memoria,
    auditar,
    calcular_puntaje,
    cambios_por_hoja,
    consolidar_cambios,
)
from .reportes import (
    escribir_base_larga,
    escribir_cambios_csv,
    escribir_hallazgos_csv,
    escribir_reporte,
    filas_base_larga,
)
from .revision import escribir_revision
from .verificacion import Verificacion, verificar_corregida
from .xlsx_xml import escribir_con_cambios


@dataclass
class Resultado:
    carpeta: Path
    nombre: str
    libro: Libro
    hallazgos_antes: list[Hallazgo]
    hallazgos_despues: list[Hallazgo]
    puntaje_antes: Puntaje
    puntaje_despues: Puntaje
    cambios: list[Cambio]
    avisos_cambios: list[str]
    verificacion: Verificacion | None
    estadisticas: dict
    archivos: dict[str, Path] = field(default_factory=dict)


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
    ruta = Path(planilla).resolve()
    if not ruta.is_file():
        raise FileNotFoundError(f"No existe el archivo {ruta}")
    cfg = config if isinstance(config, dict) else cargar_config(config)
    formato = detectar_formato(ruta)
    if formato == "desconocido":
        raise ValueError(f"{ruta.name} no es una planilla Excel (.xlsx/.xlsb/.xls)")
    informar(f"Formato real de {ruta.name}: {formato}")
    nombre = nombre_base(ruta)
    carpeta = carpeta_salida(Path(salida).resolve() if salida else ruta.parent, nombre, hoy or date.today())

    with tempfile.TemporaryDirectory(prefix="auditor_xpe_") as tmp:
        ruta_xlsx = ruta
        if formato in ("xlsb", "xls"):
            informar(f"Convirtiendo {formato} a xlsx con LibreOffice…")
            ruta_xlsx = convertir_con_libreoffice(ruta, tmp)
        libro = leer_libro(ruta_xlsx, cfg, formato=formato, ruta_original=ruta)
        informar(texto_mapeo(libro))

        antes, ctx_antes = auditar(libro, cfg)
        cambios, avisos_cambios = consolidar_cambios(libro, antes)
        libro_despues = aplicar_en_memoria(libro, cambios)
        despues, _ = auditar(libro_despues, cfg, incluir_cache=False)
        archivos: dict[str, Path] = {}

        verificacion = None
        libro_base, ctx_base, hallazgos_base = libro, ctx_antes, antes
        if not solo_reporte:
            corregida = carpeta / f"{nombre}_CORREGIDA.xlsx"
            por_hoja = cambios_por_hoja(cambios)
            escribir_con_cambios(ruta_xlsx, corregida, por_hoja)
            archivos["corregida"] = corregida
            archivos["cambios"] = carpeta / "cambios_aplicados.csv"
            escribir_cambios_csv(archivos["cambios"], cambios)
            informar("Verificando el archivo corregido…")
            verificacion = verificar_corregida(ruta_xlsx, corregida, por_hoja, con_libreoffice, cfg)
            # El "después" oficial es el que da volver a leer y auditar el archivo escrito.
            libro_c = leer_libro(corregida, cfg, formato="xlsx", ruta_original=ruta)
            reauditoria, ctx_c = auditar(libro_c, cfg, incluir_cache=False)
            a, b = {h.firma() for h in despues}, {h.firma() for h in reauditoria}
            verificacion.reauditoria_coincide = a == b
            verificacion.diferencias_reauditoria = [" / ".join(map(str, x)) for x in sorted(a ^ b)][:20]
            despues = reauditoria
            libro_base, ctx_base, hallazgos_base = libro_c, ctx_c, reauditoria

        p_antes = calcular_puntaje(libro, antes, cfg)
        p_despues = calcular_puntaje(libro_despues, despues, cfg)
        aplicados = set() if solo_reporte else {(c.hoja, c.celda) for c in cambios}
        pendientes = {h.firma() for h in despues}
        estadisticas = ctx_antes.estadisticas

        archivos["revision"] = carpeta / f"{nombre}_REVISION.xlsx"
        informar("Escribiendo el archivo de revisión…")
        escribir_revision(ruta_xlsx, archivos["revision"], ruta.name, formato, antes, despues, p_antes,
                          p_despues, aplicados, cfg, estadisticas, len(cambios))
        archivos["hallazgos"] = carpeta / "hallazgos.csv"
        escribir_hallazgos_csv(archivos["hallazgos"], antes, aplicados, pendientes)
        archivos["base"] = carpeta / "base_limpia_larga.csv"
        escribir_base_larga(archivos["base"], filas_base_larga(libro_base, ctx_base, hallazgos_base, cfg))
        archivos["reporte"] = carpeta / "reporte.md"
        orden = ["revision", "corregida", "cambios", "reporte", "base", "hallazgos"]
        nombres = [archivos[k].name for k in orden if k in archivos]
        escribir_reporte(
            archivos["reporte"], nombre=nombre, libro=libro, antes=p_antes, despues=p_despues,
            hallazgos_antes=antes, hallazgos_despues=despues, cambios=cambios, avisos_cambios=avisos_cambios,
            verificacion=verificacion, estadisticas=estadisticas, config=cfg, solo_reporte=solo_reporte,
            archivos=nombres,
        )
    return Resultado(carpeta, nombre, libro, antes, despues, p_antes, p_despues, cambios, avisos_cambios,
                     verificacion, estadisticas, archivos)


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
