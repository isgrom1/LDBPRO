"""Servidor local de la aplicación: API JSON + archivos de la interfaz.

Solo escucha en 127.0.0.1. Los pedidos que modifican algo exigen la cabecera X-Auditor (una
página de otro sitio no puede enviarla sin permiso CORS) y se valida el Host para evitar
ataques de «DNS rebinding».
"""

from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import os
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import zipfile
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from .. import __version__
from ..cli import carpeta_salida
from ..config import cargar_config
from ..demo import crear_planilla_demo
from ..formato import nombre_base, soffice_disponible
from ..hallazgos import REGLAS, Hallazgo
from ..modelo import HOJA_ESTACIONES, Libro
from ..motor import Puntaje
from ..reportes import resumen_origen
from ..sesion import (
    GRUPOS_ORIGEN,
    Analisis,
    Decision,
    ErrorDecision,
    Resultado,
    analizar,
    es_editable,
    exclusiones,
    generar,
    preparar_decision,
    proyectar,
    tipo_de_columna,
    ubicar,
)
from ..texto import a_fecha, a_hora, limpiar, mostrar

ESTATICOS = Path(__file__).resolve().parent / "static"
MAX_SUBIDA = 200 * 1024 * 1024
# Reglas donde escribir otro valor en la celda no resuelve nada: se decide con «está bien así»
# o con las acciones sugeridas (p. ej. una estación sin registros o un duplicado).
SIN_EDICION = {"E12", "R08", "R11", "R15", "R25", "T01", "X01", "X02"}
DESCRIPCIONES = {
    "corregida": "La planilla original con las correcciones. Conserva listas desplegables, fórmulas y formato.",
    "revision": "Copia para mirar: celdas coloreadas y comentadas, hojas RESUMEN y HALLAZGOS.",
    "reporte": "Resumen legible: puntaje, qué se corrigió y qué falta, por estación.",
    "base": "Tabla larga lista para R o PAST, con las uniones rehechas y columna excluir_motivo.",
    "cambios": "Cada celda modificada: valor anterior, valor nuevo y regla.",
    "hallazgos": "Todos los hallazgos en formato CSV.",
}


def _hash(texto: str) -> str:
    return hashlib.sha1(texto.encode("utf-8")).hexdigest()[:12]


def _puntaje(p: Puntaje) -> dict:
    return {
        "indice": round(p.indice, 1), "estado": p.estado,
        "pct_registros": round(p.pct_registros, 1), "pct_estaciones": round(p.pct_estaciones, 1),
        "n_registros": p.n_registros, "n_registros_limpios": p.n_registros_limpios,
        "n_estaciones": p.n_estaciones, "n_estaciones_limpias": p.n_estaciones_limpias,
        "reglas_criticas": p.reglas_criticas,
        "por_nivel": {s.split()[0]: n for s, n in p.por_severidad.items()},
    }


def _contexto(libro: Libro, h: Hallazgo) -> str:
    """Resumen de la fila del hallazgo, para verlo sin abrir Excel."""
    if h.fila is None:
        return ""
    try:
        t = libro.tabla(h.hoja)
    except KeyError:
        return ""
    if h.fila not in t.valores.index:
        return ""
    v = lambda c: t.valor(h.fila, c)  # noqa: E731
    if h.hoja == HOJA_ESTACIONES:
        partes = [limpiar(v("id")), limpiar(v("ambiente")) or "sin ambiente", f"ladera {limpiar(v('ladera')) or '—'}"]
        if v("utm_e") is not None:
            partes.append(f"E {mostrar(v('utm_e'))} · S {mostrar(v('utm_s'))}")
        return " · ".join(p for p in partes if p)
    if t.tiene("fecha"):
        fecha, hora = a_fecha(v("fecha")), a_hora(v("hora"))
        partes = [
            limpiar(v("id")),
            (f"{fecha:%d-%m-%Y}" if fecha else "sin fecha") + (f" {hora:%H:%M}" if hora else ""),
            limpiar(v("metodologia")), limpiar(v("tipo_registro")), limpiar(v("nombre_comun")),
            f"abundancia {mostrar(v('abundancia'))}" if v("abundancia") is not None else "",
        ]
        return " · ".join(p for p in partes if p)
    return ""


class SesionApp:
    def __init__(self, id_: str, carpeta: Path, archivo: str, ruta: Path, sha256: str):
        self.id, self.carpeta, self.archivo, self.ruta, self.sha256 = id_, carpeta, archivo, ruta, sha256
        self.analisis: Analisis | None = None
        self.decisiones: dict[str, Decision] = {}
        self.ids: dict[str, str] = {}  # id corto -> clave del hallazgo
        self.estado = "analizando"
        self.pasos: list[str] = []
        self.error: str | None = None
        self.resultado: Resultado | None = None
        self.restauradas = 0
        # Domésticas / exóticas dentro o fuera de base_limpia_larga.csv; None = lo del config
        self.excluir: dict[str, bool] | None = None
        self.version = 0
        self._proyeccion: tuple[int, dict] | None = None
        self.lock = threading.RLock()

    # -------------------------------------------------------------- persistencia
    def guardar(self) -> None:
        datos = {
            "app": "Auditor XPE", "version": __version__, "archivo": self.archivo,
            "original": self.ruta.relative_to(self.carpeta).as_posix(), "sha256": self.sha256,
            "actualizado": datetime.now().isoformat(timespec="seconds"),
            "decisiones": {k: d.a_dict() for k, d in self.decisiones.items()},
        }
        if self.excluir is not None:
            datos["excluir_del_analisis"] = self.excluir
        if self.analisis:
            datos["indice_antes"] = round(self.analisis.puntaje_antes.indice, 1)
            datos["estado_antes"] = self.analisis.puntaje_antes.estado
        if self.resultado:
            datos["indice_despues"] = round(self.resultado.puntaje_despues.indice, 1)
            datos["estado_despues"] = self.resultado.puntaje_despues.estado
        temporal = self.carpeta / "sesion.json.tmp"
        temporal.write_text(json.dumps(datos, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        temporal.replace(self.carpeta / "sesion.json")

    # -------------------------------------------------------------- vistas JSON
    def resumen(self, con_datos: bool = False) -> dict:
        with self.lock:
            out = {"id": self.id, "archivo": self.archivo, "estado": self.estado, "pasos": list(self.pasos),
                   "error": self.error, "carpeta": str(self.carpeta)}
            if con_datos and self.analisis and self.estado != "analizando":
                out["datos"] = self.datos()
            if self.resultado and self.estado == "generado":
                out["resultado"] = self.datos_resultado()
            return out

    def datos(self) -> dict:
        a = self.analisis
        libro = a.libro
        hallazgos = []
        for h in a.antes:
            id_ = _hash(h.clave())
            editable = h.id_regla not in SIN_EDICION and es_editable(libro, h.hoja, h.celda)
            columna = tipo = None
            if editable:
                t, _, clave = ubicar(libro, h.hoja, h.celda)
                columna, tipo = t.encabezados[clave], tipo_de_columna(clave)
                if clave == "nombre_comun" and h.hoja != "nombres BD":
                    tipo = "especie"
            hallazgos.append({
                "id": id_, "regla": h.id_regla, "titulo": REGLAS[h.id_regla].titulo if h.id_regla in REGLAS else "",
                "nivel": h.severidad.nivel, "hoja": h.hoja, "celda": h.celda, "fila": h.fila,
                "estacion": h.id_estacion, "valor": mostrar(h.valor_actual) if h.valor_actual is not None else "",
                "problema": h.problema, "sugerencia": h.sugerencia,
                "auto": mostrar(h.correccion.valor_nuevo) if h.correccion else None,
                "acciones": [x.etiqueta for x in h.acciones], "opciones": h.opciones,
                "editable": editable, "tipo": tipo, "columna": columna, "contexto": _contexto(libro, h),
                "motivo": None if editable else ("sin_edicion" if h.id_regla in SIN_EDICION
                                                 else "formula" if h.celda and h.fila else "general"),
            })
        est = libro.estaciones
        estaciones = [
            {"id": limpiar(est.valor(f, "id")), "ambiente": limpiar(est.valor(f, "ambiente")), "fila": f}
            for f in est.filas_datos
        ]
        r11 = a.ctx.estadisticas.get("R11", {})
        return {
            "archivo": self.archivo, "nombre": a.nombre, "formato": a.formato,
            "n_estaciones": len(est.filas_datos),
            "n_registros": sum(len(t.filas_datos) for t in libro.registros),
            "hojas_registros": [{"hoja": t.hoja, "n": len(t.filas_datos)} for t in libro.registros],
            "hojas_sin_datos": libro.registros_sin_datos, "avisos": libro.avisos,
            "puntaje_antes": _puntaje(a.puntaje_antes), "puntaje_auto": _puntaje(a.puntaje_despues),
            "hallazgos": hallazgos, "estaciones": estaciones,
            "reglas": {r.id: {"titulo": r.titulo, "nivel": r.severidad.nivel, "auto": r.auto_corregible}
                       for r in REGLAS.values()},
            "aves": next(iter(r11.values()), None),
            "sin_registros": [h.id_estacion for h in a.antes if h.id_regla == "E12"],
            "nombres_bd": sorted({limpiar(libro.nombres_bd.valor(f, "nombre_comun")) for f in libro.nombres_bd.filas_datos}),
            "decisiones": {_hash(k): self._decision_json(d) for k, d in self.decisiones.items()},
            "restauradas": self.restauradas, "n_cambios_auto": len(a.cambios_auto),
            "origen": {"especies": resumen_origen(a.ctx, a.config), "excluir": exclusiones(a.config, self.excluir)},
        }

    @staticmethod
    def _decision_json(d: Decision) -> dict:
        return {"tipo": d.tipo, "valor": mostrar(d.valor) if d.tipo == "valor" else None,
                "texto": d.texto, "nota": d.nota, "hoja": d.hoja, "celda": d.celda, "descripcion": d.describir()}

    def datos_resultado(self) -> dict:
        r = self.resultado
        v = r.verificacion
        archivos = []
        for clave, ruta in r.archivos.items():
            if ruta.exists():
                archivos.append({"clave": clave, "nombre": ruta.name, "descripcion": DESCRIPCIONES.get(clave, ""),
                                 "bytes": ruta.stat().st_size})
        verificacion = None
        if v is not None:
            verificacion = {
                "listas": [sum(v.x14_original.values()), sum(v.x14_corregida.values()), v.x14_ok],
                "formulas": [sum(v.formulas_original.values()), sum(v.formulas_corregida.values()), v.formulas_ok],
                "celdas": [len(v.celdas_distintas), len(v.celdas_esperadas), v.celdas_ok],
                "estilos": v.estilos_identicos, "reauditoria": v.reauditoria_coincide,
                "libreoffice": None if v.libreoffice is None else not v.libreoffice["errores_nuevos"],
                "todo_ok": v.todo_ok,
            }
        pendientes = [h for h in r.hallazgos_despues
                      if h.clave() not in {k for k, d in r.decisiones.items() if d.tipo == "ok"}]
        return {
            "puntaje_antes": _puntaje(r.puntaje_antes), "puntaje_despues": _puntaje(r.puntaje_despues),
            "n_cambios": len(r.cambios), "n_manuales": sum(any(x.startswith("MANUAL") for x in c.reglas) for c in r.cambios),
            "aceptados": r.aceptados, "archivos": archivos, "verificacion": verificacion,
            "pendientes": {n: sum(1 for h in pendientes if h.severidad.nivel == n) for n in (3, 2, 1)},
        }

    def proyeccion(self) -> dict:
        with self.lock:
            if self._proyeccion and self._proyeccion[0] == self.version:
                return self._proyeccion[1]
            version, decisiones = self.version, dict(self.decisiones)
        hallazgos, p = proyectar(self.analisis, decisiones)
        ok = {k for k, d in decisiones.items() if d.tipo == "ok"}
        pendientes = [h for h in hallazgos if h.clave() not in ok]
        datos = {"puntaje": _puntaje(p), "version": version,
                 "pendientes": {n: sum(1 for h in pendientes if h.severidad.nivel == n) for n in (3, 2, 1)}}
        with self.lock:
            self._proyeccion = (version, datos)
        return datos


class Aplicacion:
    def __init__(self, carpeta_base: Path, config: dict | None = None, con_libreoffice: bool = True):
        self.carpeta_base = Path(carpeta_base)
        self.carpeta_base.mkdir(parents=True, exist_ok=True)
        self.config = config or cargar_config()
        self.con_libreoffice = con_libreoffice
        self.sesiones: dict[str, SesionApp] = {}
        self.lock = threading.Lock()
        self.ultimo_ping = time.monotonic()
        self.cerrar = threading.Event()
        # Modo ventana propia (pywebview) o navegador; lo fija el lanzador.
        self.escritorio = False
        self.al_mostrar = None  # función que trae la ventana al frente

    # -------------------------------------------------------------- sesiones
    def _sesion_previa(self, sha256: str) -> Path | None:
        candidatas = []
        for f in self.carpeta_base.glob("*/sesion.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if d.get("sha256") == sha256 and (f.parent / d.get("original", "")).is_file():
                candidatas.append((f.stat().st_mtime, f.parent))
        return max(candidatas)[1] if candidatas else None

    def nueva_sesion(self, nombre: str, contenido: bytes) -> SesionApp:
        nombre = Path(nombre).name or "planilla.xlsx"
        sha = hashlib.sha256(contenido).hexdigest()
        previa = self._sesion_previa(sha)
        if previa is not None:
            return self.abrir_sesion(previa)
        carpeta = carpeta_salida(self.carpeta_base, nombre_base(nombre), date.today())
        ruta = carpeta / "original" / nombre
        ruta.parent.mkdir(parents=True)
        ruta.write_bytes(contenido)
        return self._iniciar(SesionApp(_hash(f"{carpeta}{time.time()}"), carpeta, nombre, ruta, sha))

    def abrir_sesion(self, carpeta: Path) -> SesionApp:
        carpeta = Path(carpeta).resolve()
        if self.carpeta_base.resolve() not in carpeta.parents:
            raise ValueError("Carpeta fuera del área de trabajo")
        for s in self.sesiones.values():
            if s.carpeta.resolve() == carpeta and s.estado != "error":
                return s
        d = json.loads((carpeta / "sesion.json").read_text(encoding="utf-8"))
        s = SesionApp(_hash(f"{carpeta}{time.time()}"), carpeta, d["archivo"], carpeta / d["original"], d["sha256"])
        s.decisiones = {k: Decision.de_dict(v) for k, v in d.get("decisiones", {}).items()}
        s.restauradas = len(s.decisiones)
        s.excluir = d.get("excluir_del_analisis")
        return self._iniciar(s)

    def _iniciar(self, s: SesionApp) -> SesionApp:
        with self.lock:
            self.sesiones[s.id] = s
        threading.Thread(target=self._analizar, args=(s,), daemon=True).start()
        return s

    def _analizar(self, s: SesionApp) -> None:
        def paso(m: str):
            with s.lock:
                s.pasos.append(m)
        try:
            a = analizar(s.ruta, self.config, s.carpeta / "original", paso)
            with s.lock:
                s.analisis = a
                s.ids = {_hash(h.clave()): h.clave() for h in a.antes}
                validas = {h.clave() for h in a.antes}
                s.decisiones = {k: d for k, d in s.decisiones.items() if k in validas}
                s.restauradas = len(s.decisiones) if s.restauradas else 0
                s.estado = "listo"
                s.pasos.append("Listo")
            s.guardar()
        except Exception as exc:  # se muestra en pantalla
            traceback.print_exc()
            with s.lock:
                s.estado, s.error = "error", _mensaje_error(exc)

    def generar(self, s: SesionApp) -> None:
        with s.lock:
            if s.estado in ("analizando", "generando"):
                raise ValueError("La sesión está ocupada")
            s.estado, s.pasos = "generando", []
        threading.Thread(target=self._generar, args=(s,), daemon=True).start()

    def _generar(self, s: SesionApp) -> None:
        def paso(m: str):
            with s.lock:
                s.pasos.append(m)
        try:
            r = generar(s.analisis, s.carpeta, dict(s.decisiones), con_libreoffice=self.con_libreoffice,
                        informar=paso, excluir=s.excluir)
            with s.lock:
                s.resultado, s.estado = r, "generado"
                s.pasos.append("Listo")
            s.guardar()
        except Exception as exc:
            traceback.print_exc()
            with s.lock:
                s.estado, s.error = "listo", _mensaje_error(exc)

    def recientes(self, n: int = 8) -> list[dict]:
        out = []
        for f in self.carpeta_base.glob("*/sesion.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            out.append({
                "carpeta": str(f.parent), "archivo": d.get("archivo"), "actualizado": d.get("actualizado"),
                "estado": d.get("estado_despues") or d.get("estado_antes"),
                "indice": d.get("indice_despues", d.get("indice_antes")),
                "decisiones": len(d.get("decisiones", {})), "_t": f.stat().st_mtime,
            })
        out.sort(key=lambda x: x.pop("_t"), reverse=True)
        return out[:n]


def _mensaje_error(exc: Exception) -> str:
    if isinstance(exc, PermissionError):
        return (f"No se pudo escribir «{Path(getattr(exc, 'filename', '') or '').name}». "
                "Si está abierto en Excel, ciérralo y vuelve a intentarlo.")
    return str(exc) or exc.__class__.__name__


def abrir_en_explorador(ruta: Path) -> bool:
    try:
        if sys.platform.startswith("win"):
            os.startfile(ruta)  # noqa: S606 (Windows)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(ruta)])
        elif shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", str(ruta)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            return False
        return True
    except OSError:
        return False


class Manejador(BaseHTTPRequestHandler):
    server_version = "AuditorXPE"
    app: Aplicacion  # se asigna al crear el servidor

    def log_message(self, formato, *args):  # silencioso: la ventana no muestra consola
        pass

    # -------------------------------------------------------------- utilidades
    def _host_valido(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _json(self, datos, estado=HTTPStatus.OK):
        cuerpo = json.dumps(datos, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(estado)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def _error(self, mensaje: str, estado=HTTPStatus.BAD_REQUEST):
        self._json({"error": mensaje}, estado)

    def _leer_cuerpo(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_SUBIDA:
            raise ValueError("El archivo es demasiado grande")
        return self.rfile.read(n) if n else b""

    def _leer_json(self) -> dict:
        cuerpo = self._leer_cuerpo()
        return json.loads(cuerpo.decode("utf-8")) if cuerpo else {}

    def _archivo(self, ruta: Path, descarga: str | None = None, tipo: str | None = None):
        self._bytes(ruta.read_bytes(), tipo or mimetypes.guess_type(ruta.name)[0], descarga)

    def _bytes(self, datos: bytes, tipo: str | None, descarga: str | None = None):
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", tipo or "application/octet-stream")
        self.send_header("Content-Length", str(len(datos)))
        if descarga:
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(descarga)}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(datos)

    def _sesion(self, id_: str) -> SesionApp | None:
        s = self.app.sesiones.get(id_)
        if s is None:
            self._error("La sesión ya no existe: vuelve a abrir la planilla", HTTPStatus.NOT_FOUND)
        return s

    # -------------------------------------------------------------- GET
    def do_GET(self):
        if not self._host_valido():
            return self._error("Host no permitido", HTTPStatus.FORBIDDEN)
        partes = [unquote(p) for p in urlparse(self.path).path.strip("/").split("/") if p]
        try:
            if not partes or partes == ["index.html"]:
                return self._archivo(ESTATICOS / "index.html", tipo="text/html; charset=utf-8")
            if partes[0] == "static" and len(partes) == 2:
                ruta = (ESTATICOS / partes[1]).resolve()
                if ruta.parent != ESTATICOS.resolve() or not ruta.is_file():
                    return self._error("No existe", HTTPStatus.NOT_FOUND)
                tipos = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                         ".svg": "image/svg+xml"}
                return self._archivo(ruta, tipo=tipos.get(ruta.suffix))
            if partes == ["api", "info"]:
                return self._json({"app": "Auditor XPE", "version": __version__, "escritorio": self.app.escritorio,
                                   "carpeta": str(self.app.carpeta_base), "libreoffice": bool(soffice_disponible())})
            if partes == ["api", "recientes"]:
                return self._json(self.app.recientes())
            if len(partes) >= 3 and partes[:2] == ["api", "sesiones"]:
                s = self._sesion(partes[2])
                if s is None:
                    return None
                if len(partes) == 3:
                    return self._json(s.resumen(con_datos="datos=1" in urlparse(self.path).query))
                if partes[3] == "proyeccion":
                    if s.analisis is None:
                        return self._error("Todavía se está analizando")
                    return self._json(s.proyeccion())
                if partes[3] == "zip" and s.resultado:
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                        for ruta in s.resultado.archivos.values():
                            if ruta.exists():
                                z.write(ruta, ruta.name)
                    return self._bytes(buf.getvalue(), "application/zip", f"auditoria_{s.analisis.nombre}.zip")
                if partes[3] == "archivos" and len(partes) == 5 and s.resultado:
                    ruta = next((r for r in s.resultado.archivos.values() if r.name == partes[4]), None)
                    if ruta and ruta.exists():
                        return self._archivo(ruta, ruta.name)
            return self._error("No existe", HTTPStatus.NOT_FOUND)
        except (BrokenPipeError, ConnectionResetError):
            return None
        except Exception as exc:
            traceback.print_exc()
            return self._error(_mensaje_error(exc), HTTPStatus.INTERNAL_SERVER_ERROR)

    # -------------------------------------------------------------- POST / PUT / DELETE
    def _modificar(self, metodo: str):
        if not self._host_valido() or self.headers.get("X-Auditor") != "1":
            return self._error("Pedido no permitido", HTTPStatus.FORBIDDEN)
        partes = [unquote(p) for p in urlparse(self.path).path.strip("/").split("/") if p]
        try:
            if metodo == "POST" and partes == ["api", "ping"]:
                self.app.ultimo_ping = time.monotonic()
                return self._json({"ok": True})
            if metodo == "POST" and partes == ["api", "salir"]:
                self.app.cerrar.set()
                return self._json({"ok": True})
            if metodo == "POST" and partes == ["api", "mostrar"]:  # otra copia del programa pide el frente
                if self.app.al_mostrar:
                    self.app.al_mostrar()
                return self._json({"ok": True})
            if metodo == "POST" and partes == ["api", "abrir-carpeta"]:
                return self._json({"ok": abrir_en_explorador(self.app.carpeta_base)})
            if metodo == "POST" and partes == ["api", "auditar"]:
                nombre = unquote(self.headers.get("X-Archivo") or "planilla.xlsx")
                contenido = self._leer_cuerpo()
                if not contenido:
                    return self._error("No llegó ningún archivo")
                return self._json(self.app.nueva_sesion(nombre, contenido).resumen())
            if metodo == "POST" and partes == ["api", "ejemplo"]:
                with tempfile.TemporaryDirectory() as tmp:
                    ruta = crear_planilla_demo(Path(tmp) / "planilla_ejemplo.xlsx")
                    return self._json(self.app.nueva_sesion(ruta.name, ruta.read_bytes()).resumen())
            if metodo == "POST" and partes == ["api", "abrir"]:
                return self._json(self.app.abrir_sesion(Path(self._leer_json()["carpeta"])).resumen())
            if len(partes) >= 4 and partes[:2] == ["api", "sesiones"]:
                s = self._sesion(partes[2])
                if s is None:
                    return None
                accion = partes[3]
                if metodo == "POST" and accion == "generar":
                    self.app.generar(s)
                    return self._json(s.resumen())
                if metodo == "POST" and accion == "abrir-carpeta":
                    return self._json({"ok": abrir_en_explorador(s.carpeta)})
                if metodo == "POST" and accion == "abrir-archivo" and s.resultado:
                    nombre = self._leer_json().get("nombre")
                    ruta = next((r for r in s.resultado.archivos.values() if r.name == nombre), None)
                    if ruta is None or not ruta.exists():
                        return self._error("Ese archivo ya no existe", HTTPStatus.NOT_FOUND)
                    return self._json({"ok": abrir_en_explorador(ruta)})
                if metodo == "POST" and accion == "origen" and s.analisis is not None:
                    elegidas = self._leer_json()
                    if not elegidas or any(g not in GRUPOS_ORIGEN or not isinstance(v, bool) for g, v in elegidas.items()):
                        return self._error("Elección no válida")
                    with s.lock:
                        s.excluir = exclusiones(s.analisis.config, {**(s.excluir or {}), **elegidas})
                    s.guardar()
                    return self._json({"ok": True, "excluir": s.excluir})
                if metodo == "POST" and accion == "reiniciar":
                    with s.lock:
                        s.decisiones.clear()
                        s.version += 1
                        s.restauradas = 0
                    s.guardar()
                    return self._json({"ok": True})
                if accion == "decisiones" and len(partes) == 5 and s.analisis is not None:
                    clave = s.ids.get(partes[4])
                    if clave is None:
                        return self._error("Hallazgo desconocido", HTTPStatus.NOT_FOUND)
                    with s.lock:
                        if metodo == "DELETE":
                            s.decisiones.pop(clave, None)
                            d = None
                        else:
                            d = preparar_decision(s.analisis, s.analisis.por_clave[clave], self._leer_json())
                            s.decisiones[clave] = d
                        s.version += 1
                    s.guardar()
                    return self._json({"ok": True, "decision": SesionApp._decision_json(d) if d else None})
            return self._error("No existe", HTTPStatus.NOT_FOUND)
        except ErrorDecision as exc:
            return self._error(str(exc))
        except (BrokenPipeError, ConnectionResetError):
            return None
        except Exception as exc:
            traceback.print_exc()
            return self._error(_mensaje_error(exc), HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self):
        return self._modificar("POST")

    def do_PUT(self):
        return self._modificar("PUT")

    def do_DELETE(self):
        return self._modificar("DELETE")


class ServidorLocal(ThreadingHTTPServer):
    """Servidor que no comparte su puerto.

    HTTPServer activa SO_REUSEADDR y en Windows eso deja que dos copias del programa escuchen
    en el mismo puerto: los pedidos llegan a cualquiera y, si una se cierra, la interfaz pierde
    la conexión («Failed to fetch»). Aquí el puerto es exclusivo.
    """

    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        # Sin socket.getfqdn() de HTTPServer.server_bind: en algunos Windows demora segundos.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def crear_servidor(app: Aplicacion, puerto: int = 0) -> ThreadingHTTPServer:
    manejador = type("ManejadorApp", (Manejador,), {"app": app})
    return ServidorLocal(("127.0.0.1", puerto), manejador)
