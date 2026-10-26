"""Punto de entrada de la aplicación (lo que ejecuta AuditorXPE.exe).

La interfaz se muestra en una ventana propia (pywebview con WebView2, el motor de Edge que
trae Windows 10 y 11). El trabajo lo hace un servidor local, en un puerto libre que solo
atiende a este equipo. Si la ventana propia no se puede abrir, se usa el navegador.
"""

from __future__ import annotations

import argparse
import json
import os
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

INACTIVIDAD_MAX = 30 * 60  # modo navegador: segundos sin la interfaz abierta antes de cerrarse solo
ARCHIVO_INSTANCIA = "instancia.json"
_MUTEX = None  # se mantiene vivo mientras corre el programa


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


# --------------------------------------------------------------------------- una sola copia


def _primera_instancia() -> bool:
    """En Windows, un mutex con nombre garantiza que haya una sola copia del programa abierta."""
    global _MUTEX
    if not sys.platform.startswith("win"):
        return True
    import ctypes

    kernel32 = ctypes.windll.kernel32
    _MUTEX = kernel32.CreateMutexW(None, False, "Local\\AuditorXPE_unica")
    return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def _avisar_a_la_abierta(carpeta: Path) -> bool:
    """Pide a la copia que ya está abierta que traiga su ventana al frente."""
    try:
        puerto = json.loads((carpeta / ARCHIVO_INSTANCIA).read_text(encoding="utf-8"))["puerto"]
        pedido = urllib.request.Request(
            f"http://127.0.0.1:{puerto}/api/mostrar", data=b"{}", method="POST",
            headers={"X-Auditor": "1", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(pedido, timeout=3) as r:
            r.read()
        return True
    except (OSError, ValueError, KeyError):
        return False


def _mensaje(texto: str) -> None:
    if sys.platform.startswith("win"):
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, texto, "Auditor XPE", 0x40)
    else:
        print(texto)


# --------------------------------------------------------------------------- ventanas


def _ventana_propia(app: Aplicacion, url: str) -> bool:
    """Muestra la interfaz en una ventana nativa. False si pywebview o WebView2 no están."""
    try:
        import webview
    except Exception:
        return False
    try:
        webview.settings["ALLOW_DOWNLOADS"] = True
        ventana = webview.create_window(
            "Auditor XPE", url, width=1360, height=880, min_size=(980, 640), text_select=True
        )
    except Exception:
        traceback.print_exc()
        return False

    def al_mostrar():
        try:
            ventana.restore()
            ventana.show()
            ventana.on_top = True
            ventana.on_top = False
        except Exception:
            pass

    def cerrar_si_lo_piden():  # botón «Cerrar» de la interfaz
        app.cerrar.wait()
        try:
            ventana.destroy()
        except Exception:
            pass

    app.al_mostrar, app.escritorio = al_mostrar, True
    threading.Thread(target=cerrar_si_lo_piden, daemon=True).start()
    try:
        # En Windows se exige WebView2: sin él pywebview usaría Internet Explorer, que no sirve.
        webview.start(gui="edgechromium" if sys.platform.startswith("win") else None, private_mode=True)
    except Exception:
        traceback.print_exc()
        app.al_mostrar, app.escritorio = None, False
        return False
    return True


def _vigilar_inactividad(app: Aplicacion) -> None:
    while not app.cerrar.wait(30):
        ocupado = any(s.estado in ("analizando", "generando") for s in app.sesiones.values())
        if not ocupado and time.monotonic() - app.ultimo_ping > INACTIVIDAD_MAX:
            app.cerrar.set()


def _ventana_control(app: Aplicacion, url: str) -> bool:
    """Respaldo del modo navegador: ventanita para reabrir la interfaz o cerrar el programa.

    Devuelve False si no hay entorno gráfico.
    """
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
            pedir("PUT", f"/api/sesiones/{s['id']}/decisiones/{r06['id']}",
                  {"tipo": "accion", "indice": r06["acciones"].index("Poner 1")})
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


def autoprueba_ventana(registro: Path) -> int:
    """Abre la ventana propia, carga la planilla de ejemplo desde la interfaz y la cierra."""
    lineas = []

    def anotar(texto):
        lineas.append(texto)
        registro.write_text("\n".join(lineas), encoding="utf-8")
        print(texto, flush=True)

    try:
        import webview
    except Exception:
        anotar("pywebview no está instalado\n" + traceback.format_exc())
        return 1
    resultado = {"ok": False}
    with tempfile.TemporaryDirectory(prefix="autoprueba_ventana_") as tmp:
        app = Aplicacion(Path(tmp) / "Auditor XPE", con_libreoffice=False)
        servidor = crear_servidor(app, 0)
        peticiones = []
        base = servidor.RequestHandlerClass
        servidor.RequestHandlerClass = type(
            "ManejadorAnotado", (base,),
            {"log_message": lambda self, formato, *args: peticiones.append(formato % args)},
        )
        threading.Thread(target=servidor.serve_forever, daemon=True).start()
        ventana = webview.create_window("Auditor XPE (autoprueba)", f"http://127.0.0.1:{servidor.server_address[1]}/")

        def esperar(expresion, segundos):
            limite = time.monotonic() + segundos
            while time.monotonic() < limite:
                if ventana.evaluate_js(expresion):
                    return True
                time.sleep(0.5)
            return False

        def probar():
            try:
                cargo = esperar("document.body && document.body.dataset.listo === '1'", 60)
                anotar(f"Interfaz cargada en la ventana: {cargo}")
                ventana.evaluate_js("document.querySelector('#btn-ejemplo').click()")
                revision = esperar("!document.querySelector('#vista-revision').hidden", 120)
                n = ventana.evaluate_js("document.querySelectorAll('.hallazgo').length")
                anotar(f"Vista de revisión: {revision}; tarjetas de hallazgos: {n}")
                if not revision:
                    estado = ventana.evaluate_js(
                        "JSON.stringify({vista: [...document.querySelectorAll('.vista')].filter(v => !v.hidden)"
                        ".map(v => v.id), error: document.querySelector('#error-inicio').textContent,"
                        " avisos: document.querySelector('#avisos').textContent,"
                        " pasos: document.querySelector('#progreso-pasos').textContent})"
                    )
                    anotar(f"Estado de la interfaz: {estado}")
                    anotar("Peticiones recibidas: " + ", ".join(peticiones[-15:]))
                resultado["ok"] = bool(cargo and revision and n)
            except Exception:
                anotar(traceback.format_exc())
            finally:
                ventana.destroy()

        try:
            webview.start(probar, gui="edgechromium" if sys.platform.startswith("win") else None, private_mode=True)
        except Exception:
            anotar("No se pudo abrir la ventana\n" + traceback.format_exc())
        servidor.shutdown()
        servidor.server_close()
    anotar("AUTOPRUEBA VENTANA OK" if resultado["ok"] else "AUTOPRUEBA VENTANA FALLÓ")
    return 0 if resultado["ok"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="AuditorXPE", description="Auditor de planillas de fauna XPE")
    ap.add_argument("--puerto", type=int, default=0, help="Puerto del servidor local (por defecto, uno libre)")
    ap.add_argument("--carpeta", help="Carpeta donde se guardan las revisiones (por defecto Documentos/Auditor XPE)")
    ap.add_argument("--navegador", action="store_true", help="Usar el navegador en vez de la ventana propia")
    ap.add_argument("--sin-navegador", action="store_true", help="Modo servidor: no abrir ventana ni navegador")
    ap.add_argument("--sin-ventana", action="store_true", help="Sin ventana de control (Ctrl+C para salir)")
    ap.add_argument("--autoprueba", action="store_true", help="Probar la aplicación con la planilla de ejemplo y salir")
    ap.add_argument("--autoprueba-ventana", action="store_true", help="Probar la ventana propia y salir")
    ap.add_argument("--version", action="version", version=f"Auditor XPE {__version__}")
    args = ap.parse_args(argv)

    if args.autoprueba:
        return autoprueba(Path(tempfile.gettempdir()) / "AuditorXPE_autoprueba.log")
    if args.autoprueba_ventana:
        return autoprueba_ventana(Path(tempfile.gettempdir()) / "AuditorXPE_autoprueba_ventana.log")

    carpeta = Path(args.carpeta) if args.carpeta else carpeta_por_defecto()
    carpeta.mkdir(parents=True, exist_ok=True)
    if not _primera_instancia():
        if not _avisar_a_la_abierta(carpeta):
            _mensaje("El Auditor XPE ya está abierto.")
        return 0
    if sys.stdout is None or sys.stderr is None:
        # El .exe no tiene consola: los mensajes y errores quedan en registro.log
        registro = open(carpeta / "registro.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stderr = registro
    print(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} Auditor XPE {__version__}", flush=True)
    config, _ = cargar_config_usuario(carpeta)
    app = Aplicacion(carpeta, config)
    try:
        servidor = crear_servidor(app, args.puerto)
    except OSError:
        servidor = crear_servidor(app, 0)  # el puerto pedido está ocupado
    url = f"http://127.0.0.1:{servidor.server_address[1]}/"
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    (carpeta / ARCHIVO_INSTANCIA).write_text(
        json.dumps({"puerto": servidor.server_address[1], "pid": os.getpid()}), encoding="utf-8"
    )
    print(f"Servidor en {url} — resultados en {carpeta}", flush=True)
    try:
        propia = not (args.navegador or args.sin_navegador or args.sin_ventana) and _ventana_propia(app, url)
        if not propia:
            app.al_mostrar = lambda: webbrowser.open(url)
            threading.Thread(target=_vigilar_inactividad, args=(app,), daemon=True).start()
            if not args.sin_navegador:
                webbrowser.open(url)
            if args.sin_ventana or not _ventana_control(app, url):
                while not app.cerrar.wait(1):
                    pass
    except KeyboardInterrupt:
        pass
    finally:
        app.cerrar.set()
        servidor.shutdown()
        servidor.server_close()
        try:
            (carpeta / ARCHIVO_INSTANCIA).unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
