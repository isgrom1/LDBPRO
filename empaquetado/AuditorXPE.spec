# -*- mode: python ; coding: utf-8 -*-
# Receta de PyInstaller para AuditorXPE.exe (un solo archivo, sin consola).
#
#     pyinstaller --noconfirm --clean empaquetado/AuditorXPE.spec
from pathlib import Path

raiz = Path(SPECPATH).parent

a = Analysis(
    [str(raiz / "AuditorXPE.py")],
    pathex=[str(raiz)],
    datas=[
        (str(raiz / "auditor" / "app" / "static"), "auditor/app/static"),
        (str(raiz / "config.yaml"), "."),
    ],
    hiddenimports=[],
    excludes=["matplotlib", "scipy", "IPython", "pytest", "PIL", "notebook", "jinja2", "pyarrow"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AuditorXPE",
    console=False,
    upx=False,
    icon=str(raiz / "auditor" / "app" / "static" / "icono.ico"),
)
