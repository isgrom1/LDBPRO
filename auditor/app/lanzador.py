"""Punto de entrada de la aplicación (lo que ejecuta AuditorXPE.exe).

Levanta el servidor local, abre la interfaz en el navegador y muestra una ventanita de control
para reabrir la interfaz, abrir la carpeta de resultados o cerrar el programa.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path

from .. import __version__
from ..config import cargar_config
from .servidor import Aplicacion, abrir_en_explorador, crear_servidor

PUERTO = 8765
INACTIVIDAD_MAX = 30 * 60  # segundos sin la interfaz abierta antes de cerrarse solo


def carpeta_por_defecto() -> Path:
    documentos = Path.home() / "Documents"
    return (documentos if documentos.is_dir() else Path.home()) / "Auditor XPE"


def recurso(nombre: str) -> Path:
    return Path(__file__).resolve().parent / "static" / nombre


def cargar_config_usuario(carpeta: Path) -> tuple[dict, Path | None]:
    """Un config.yaml junto al .exe o en la carpeta de resultados ajusta los umbrales."""
    candidatos = [carpeta / "config.yaml"]
    if getattr(sys, "frozen", False):
        candidatos.insert(0, Path(sys.executable).parent / "config.yaml")
    for c in candidatos:
        if c.is_file():
            return cargar_config(c), c
    return cargar_config(), None


def instancia_abierta(puerto: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/api/info", timeout=0.7) as r:
            return json.loads(r.read().decode("utf-8")).get("app") == "Auditor XPE"
    except (OSError, ValueError):
        return False


def _vigilar_inactividad(app: Aplicacion) -> None:
    while not app.cerrar.wait(30):
        ocupado = any(s.estado in ("analizando", "generando") for s in app.sesiones.values())
        if not ocupado and time.monotonic() - app.ultimo_ping > INACTIVIDAD_MAX:
            app.cerrar.set()


def _ventana(app: Aplicacion, url: str) -> bool:
    """Ventana de control. Devuelve False si no hay entorno gráfico (se sigue sin ventana)."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        return False
    try:
        raiz = tk.Tk()
    except tk.TclError:
        return False
    raiz.title("Auditor XPE")
    raiz.resizable(False, False)
    try:
        if sys.platform.startswith("win") and recurso("icono.ico").exists():
            raiz.iconbitmap(str(recurso("icono.ico")))
        elif recurso("icono.png").exists():
            raiz.iconphoto(True, tk.PhotoImage(file=str(recurso("icono.png"))))
    except tk.TclError:
        pass
    marco = ttk.Frame(raiz, padding=18)
    marco.grid()
    ttk.Label(marco, text="Auditor XPE está funcionando", font=("Segoe UI", 13, "bold")).grid(columnspan=3, sticky="w")
    ttk.Label(
        marco, text="La revisión se abrió en tu navegador.\nSi cerraste la pestaña, vuelve a abrirla aquí.",
        justify="left",
    ).grid(columnspan=3, sticky="w", pady=(4, 14))

    def cerrar():
        app.cerrar.set()

    ttk.Button(marco, text="Abrir en el navegador", command=lambda: webbrowser.open(url)).grid(row=2, column=0, padx=(0, 6))
    ttk.Button(marco, text="Carpeta de resultados", command=lambda: abrir_en_explorador(app.carpeta_base)).grid(
        row=2, column=1, padx=6
    )
    ttk.Button(marco, text="Cerrar", command=cerrar).grid(row=2, column=2, padx=(6, 0))
    ttk.Label(marco, text=f"Versión {__version__} · {url}", foreground="#777").grid(columnspan=3, sticky="w", pady=(14, 0))
    raiz.protocol("WM_DELETE_WINDOW", cerrar)

    def revisar():
        if app.cerrar.is_set():
            raiz.destroy()
        else:
            raiz.after(400, revisar)

    revisar()
    raiz.mainloop()
    return True


def autoprueba(registro: Path) -> int:
    """Recorre la aplicación completa con la planilla de ejemplo (para verificar el .exe)."""
    lineas = []

    def anotar(texto):
        lineas.append(texto)
        registro.write_text("\n".join(lineas), encoding="utf-8")
        print(texto, flush=True)

    try:
        with tempfile.TemporaryDirectory(prefix="autoprueba_xpe_") as tmp:
            app = Aplicacion(Path(tmp) / "Auditor XPE", con_libreoffice=False)
            servidor = crear_servidor(app, 0)
            threading.Thread(target=servidor.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{servidor.server_address[1]}"

            def pedir(metodo, ruta, cuerpo=None, crudo=False):
                datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
                req = urllib.request.Request(base + ruta, data=datos, method=metodo,
                                             headers={"X-Auditor": "1", "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    contenido = r.read()
                return contenido if crudo else json.loads(contenido.decode("utf-8"))

            def esperar(id_, estado):
                limite = time.monotonic() + 300
                while time.monotonic() < limite:
                    r = pedir("GET", f"/api/sesiones/{id_}")
                    if r["estado"] == estado:
                        return r
                    if r["estado"] == "error" or r.get("error"):
                        raise RuntimeError(r["error"])
                    time.sleep(0.3)
                raise TimeoutError(estado)

            assert b"Auditor XPE" in pedir("GET", "/", crudo=True)
            for f in ("app.js", "app.css", "icono.svg"):
                assert pedir("GET", f"/static/{f}", crudo=True)
            anotar(f"Interfaz servida en {base}")
            s = pedir("POST", "/api/ejemplo", {})
            esperar(s["id"], "listo")
            datos = pedir("GET", f"/api/sesiones/{s['id']}?datos=1")["datos"]
            anotar(f"Planilla de ejemplo auditada: {len(datos['hallazgos'])} hallazgos, índice {datos['puntaje_antes']['indice']}")
            r06 = next(h for h in datos["hallazgos"] if h["regla"] == "R06")
            pedir("PUT", f"/api/sesiones/{s['id']}/decisiones/{r06['id']}", {"tipo": "accion", "indice": 0})
            proy = pedir("GET", f"/api/sesiones/{s['id']}/proyeccion")
            anotar(f"Decisión aplicada; índice proyectado {proy['puntaje']['indice']}")
            pedir("POST", f"/api/sesiones/{s['id']}/generar", {})
            res = esperar(s["id"], "generado")["resultado"]
            zip_ = pedir("GET", f"/api/sesiones/{s['id']}/zip", crudo=True)
            assert zip_[:2] == b"PK" and res["verificacion"]["todo_ok"], res["verificacion"]
            anotar(f"Archivos generados: {', '.join(a['nombre'] for a in res['archivos'])}")
            anotar(f"Índice {res['puntaje_antes']['indice']} -> {res['puntaje_despues']['indice']}; verificación OK")
            servidor.shutdown()
            servidor.server_close()
        anotar("AUTOPRUEBA OK")
        return 0
    except Exception:
        anotar("AUTOPRUEBA FALLÓ\n" + traceback.format_exc())
        return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="AuditorXPE", description="Auditor de planillas de fauna XPE")
    ap.add_argument("--puerto", type=int, default=PUERTO)
    ap.add_argument("--carpeta", help="Carpeta donde se guardan las revisiones (por defecto Documentos/Auditor XPE)")
    ap.add_argument("--sin-navegador", action="store_true", help="No abrir el navegador")
    ap.add_argument("--sin-ventana", action="store_true", help="Sin ventana de control (Ctrl+C para salir)")
    ap.add_argument("--autoprueba", action="store_true", help="Probar la aplicación con la planilla de ejemplo y salir")
    ap.add_argument("--version", action="version", version=f"Auditor XPE {__version__}")
    args = ap.parse_args(argv)

    if args.autoprueba:
        return autoprueba(Path(tempfile.gettempdir()) / "AuditorXPE_autoprueba.log")

    if instancia_abierta(args.puerto):
        webbrowser.open(f"http://127.0.0.1:{args.puerto}/")
        return 0
    carpeta = Path(args.carpeta) if args.carpeta else carpeta_por_defecto()
    carpeta.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:
        # El .exe no tiene consola: los mensajes y errores quedan en registro.log
        registro = open(carpeta / "registro.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stderr = registro
    config, _ = cargar_config_usuario(carpeta)
    app = Aplicacion(carpeta, config)
    try:
        servidor = crear_servidor(app, args.puerto)
    except OSError:
        servidor = crear_servidor(app, 0)  # el puerto está ocupado por otro programa
    url = f"http://127.0.0.1:{servidor.server_address[1]}/"
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    threading.Thread(target=_vigilar_inactividad, args=(app,), daemon=True).start()
    if not args.sin_navegador:
        webbrowser.open(url)
    print(f"Auditor XPE en {url} — resultados en {carpeta}", flush=True)
    try:
        if args.sin_ventana or not _ventana(app, url):
            while not app.cerrar.wait(1):
                pass
    except KeyboardInterrupt:
        pass
    servidor.shutdown()
    servidor.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
