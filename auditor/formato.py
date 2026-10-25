"""Detección del formato real del archivo (a veces un .xlsb llega como .xlsb.xlsx)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

FIRMA_ZIP = b"PK\x03\x04"
FIRMA_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

PERFIL_RECALCULO = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="OOXMLRecalcMode" oor:op="fuse"><value>0</value></prop></item>
<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="ODFRecalcMode" oor:op="fuse"><value>0</value></prop></item>
</oor:items>
"""


def detectar_formato(ruta: str | Path) -> str:
    """'xlsx', 'xlsm', 'xlsb', 'xls' o 'desconocido', según el contenido y no la extensión."""
    with open(ruta, "rb") as f:
        cabecera = f.read(8)
    if cabecera.startswith(FIRMA_OLE):
        return "xls"
    if not cabecera.startswith(FIRMA_ZIP):
        return "desconocido"
    try:
        with zipfile.ZipFile(ruta) as z:
            nombres = set(z.namelist())
            if "xl/workbook.bin" in nombres:
                return "xlsb"
            if "xl/workbook.xml" in nombres:
                tipos = z.read("[Content_Types].xml").decode("utf-8", "replace")
                return "xlsm" if "macroEnabled" in tipos else "xlsx"
    except zipfile.BadZipFile:
        return "desconocido"
    return "desconocido"


def nombre_base(ruta: str | Path) -> str:
    """Nombre sin extensiones de planilla encadenadas: 'x.xlsb.xlsx' -> 'x'."""
    nombre = Path(ruta).name
    extensiones = (".xlsx", ".xlsm", ".xlsb", ".xls")
    cambio = True
    while cambio:
        cambio = False
        for ext in extensiones:
            if nombre.lower().endswith(ext):
                nombre = nombre[: -len(ext)]
                cambio = True
    return nombre


def soffice_disponible() -> str | None:
    encontrado = shutil.which("soffice") or shutil.which("libreoffice")
    if encontrado:
        return encontrado
    candidatos = []
    if sys.platform.startswith("win"):  # en Windows LibreOffice no queda en el PATH
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if base:
                candidatos.append(Path(base) / "LibreOffice" / "program" / "soffice.exe")
    elif sys.platform == "darwin":
        candidatos.append(Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"))
    return next((str(c) for c in candidatos if c.is_file()), None)


def convertir_con_libreoffice(
    ruta: str | Path, carpeta_salida: str | Path, recalcular: bool = True, timeout: int = 300
) -> Path:
    """Convierte a .xlsx con LibreOffice headless (con recálculo forzado al abrir)."""
    soffice = soffice_disponible()
    if not soffice:
        raise RuntimeError("LibreOffice (soffice) no está instalado")
    ruta = Path(ruta).resolve()
    carpeta_salida = Path(carpeta_salida).resolve()
    carpeta_salida.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lo_perfil_") as perfil:
        if recalcular:
            (Path(perfil) / "user").mkdir(parents=True)
            (Path(perfil) / "user" / "registrymodifications.xcu").write_text(
                PERFIL_RECALCULO, encoding="utf-8"
            )
        # LibreOffice decide el filtro por la extensión: se le pasa una copia con la correcta.
        with tempfile.TemporaryDirectory(prefix="lo_entrada_") as entrada:
            formato = detectar_formato(ruta)
            ext = {"xlsb": ".xlsb", "xls": ".xls", "xlsm": ".xlsm"}.get(formato, ".xlsx")
            copia = Path(entrada) / (nombre_base(ruta) + ext)
            shutil.copyfile(ruta, copia)
            proc = subprocess.run(
                [
                    soffice, f"-env:UserInstallation={Path(perfil).as_uri()}", "--headless",
                    "--norestore", "--convert-to", "xlsx", "--outdir", str(carpeta_salida),
                    str(copia),
                ],
                capture_output=True, text=True, timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    salida = carpeta_salida / (copia.stem + ".xlsx")
    if proc.returncode != 0 or not salida.exists():
        raise RuntimeError(f"LibreOffice no pudo convertir {ruta.name}: {proc.stderr.strip()}")
    return salida
