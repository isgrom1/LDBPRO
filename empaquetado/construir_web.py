"""Genera la versión web (web/index.html y web/manifiesto.json) a partir de la interfaz del programa.

La interfaz es una sola (auditor/app/static/); la página web la reutiliza con rutas relativas y
agrega web/motor.js, que corre el mismo código Python en el navegador (Pyodide). Se publica con
GitHub Pages desde la raíz del repositorio.

    python empaquetado/construir_web.py              # escribe los archivos
    python empaquetado/construir_web.py --comprobar  # falla si no están al día (lo usan las pruebas)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ESTATICOS = "auditor/app/static/"
AVISO = "<!-- Generado por empaquetado/construir_web.py a partir de auditor/app/static/index.html: no editar. -->\n"


def pagina() -> str:
    html = (RAIZ / ESTATICOS / "index.html").read_text(encoding="utf-8")
    html = html.replace('"/static/', f'"../{ESTATICOS}')
    app_js = f'<script src="../{ESTATICOS}app.js"></script>'
    if app_js not in html:
        raise SystemExit("No encontré el <script> de app.js en index.html")
    html = html.replace(app_js, f'<script src="motor.js"></script>\n  {app_js}')
    return html.replace("<!doctype html>\n", "<!doctype html>\n" + AVISO, 1)


def manifiesto() -> str:
    archivos = sorted(p.relative_to(RAIZ).as_posix() for p in (RAIZ / "auditor").rglob("*.py")
                      if "__pycache__" not in p.parts)
    return json.dumps({"archivos": [*archivos, "config.yaml"]}, ensure_ascii=False, indent=1) + "\n"


def main(argv: list[str]) -> int:
    salidas = {RAIZ / "web" / "index.html": pagina(), RAIZ / "web" / "manifiesto.json": manifiesto()}
    if "--comprobar" in argv:
        viejos = [p.relative_to(RAIZ).as_posix() for p, t in salidas.items()
                  if not p.exists() or p.read_text(encoding="utf-8") != t]
        if viejos:
            print("Desactualizados (corre python empaquetado/construir_web.py):", ", ".join(viejos))
            return 1
        return 0
    for p, t in salidas.items():
        p.write_text(t, encoding="utf-8")
        print("Escrito", p.relative_to(RAIZ).as_posix())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
