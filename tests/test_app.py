"""Servidor de la aplicación (con la planilla de ejemplo; no necesita la planilla real)."""

from __future__ import annotations

import http.client
import io
import json
import threading
import time
import zipfile

import pytest

from auditor.app.lanzador import autoprueba
from auditor.app.servidor import Aplicacion, crear_servidor
from auditor.demo import crear_planilla_demo


class Cliente:
    def __init__(self, puerto: int):
        self.puerto = puerto

    def pedir(self, metodo, ruta, cuerpo=None, cabeceras=None, crudo=False):
        con = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=120)
        h = {"X-Auditor": "1", **(cabeceras or {})}
        if isinstance(cuerpo, (dict, list)):
            cuerpo = json.dumps(cuerpo).encode()
            h["Content-Type"] = "application/json"
        con.request(metodo, ruta, body=cuerpo, headers=h)
        r = con.getresponse()
        datos = r.read()
        con.close()
        if crudo:
            return r.status, datos, r
        return r.status, json.loads(datos.decode("utf-8")) if datos else None

    def esperar(self, id_, estado, timeout=180):
        limite = time.monotonic() + timeout
        while time.monotonic() < limite:
            _, r = self.pedir("GET", f"/api/sesiones/{id_}")
            if r["estado"] == estado:
                return r
            assert r["estado"] != "error", r["error"]
            time.sleep(0.2)
        raise TimeoutError(estado)


@pytest.fixture()
def servidor(tmp_path):
    app = Aplicacion(tmp_path / "Auditor XPE", con_libreoffice=False)
    srv = crear_servidor(app, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield app, Cliente(srv.server_address[1])
    srv.shutdown()
    srv.server_close()


def subir_demo(cliente, tmp_path):
    ruta = crear_planilla_demo(tmp_path / "mi planilla ñandú.xlsx")
    estado, r = cliente.pedir("POST", "/api/auditar", ruta.read_bytes(), {"X-Archivo": "mi%20planilla%20%C3%B1and%C3%BA.xlsx"})
    assert estado == 200, r
    cliente.esperar(r["id"], "listo")
    return r["id"], cliente.pedir("GET", f"/api/sesiones/{r['id']}?datos=1")[1]["datos"]


def test_interfaz_y_seguridad(servidor):
    _, c = servidor
    estado, html, _ = c.pedir("GET", "/", crudo=True)
    assert estado == 200 and b"Auditor XPE" in html
    assert c.pedir("GET", "/static/app.js", crudo=True)[0] == 200
    assert c.pedir("GET", "/static/../servidor.py", crudo=True)[0] == 404
    # sin la cabecera X-Auditor (lo que mandaría otra página web) se rechaza
    con = http.client.HTTPConnection("127.0.0.1", c.puerto)
    con.request("POST", "/api/salir")
    assert con.getresponse().status == 403
    # Host distinto de localhost (DNS rebinding) se rechaza
    assert c.pedir("GET", "/api/info", cabeceras={"Host": "malicioso.com"})[0] == 403
    assert c.pedir("GET", "/api/info")[1]["app"] == "Auditor XPE"


def test_flujo_completo(servidor, tmp_path):
    app, c = servidor
    id_, datos = subir_demo(c, tmp_path)
    assert datos["archivo"] == "mi planilla ñandú.xlsx"
    assert datos["n_estaciones"] == 9 and datos["n_cambios_auto"] > 0
    assert len(datos["nombres_bd"]) > 20
    por_regla = {}
    for h in datos["hallazgos"]:
        por_regla.setdefault(h["regla"], []).append(h)
    r06, e02, r08 = por_regla["R06"][0], por_regla["E02"][0], por_regla["R08"][0]
    assert r06["acciones"] == ["Poner 1"] and r06["editable"] and r06["tipo"] == "numero"
    assert not r08["editable"]  # una especie doméstica no se arregla escribiendo otro nombre
    assert r06["contexto"].startswith("QV02")

    ruta = f"/api/sesiones/{id_}/decisiones"
    assert c.pedir("PUT", f"{ruta}/{r06['id']}", {"tipo": "valor", "texto": "muchos"})[0] == 400
    estado, r = c.pedir("PUT", f"{ruta}/{r06['id']}", {"tipo": "accion", "indice": 0, "nota": "conteo mínimo"})
    assert estado == 200 and r["decision"]["valor"] == "1"
    assert c.pedir("PUT", f"{ruta}/{e02['id']}", {"tipo": "valor", "texto": "Bosque nativo"})[0] == 200
    assert c.pedir("PUT", f"{ruta}/{r08['id']}", {"tipo": "ok"})[0] == 200
    assert c.pedir("DELETE", f"{ruta}/{r08['id']}")[0] == 200
    _, proy = c.pedir("GET", f"/api/sesiones/{id_}/proyeccion")
    assert proy["puntaje"]["indice"] > datos["puntaje_auto"]["indice"]

    assert c.pedir("POST", f"/api/sesiones/{id_}/generar", {})[0] == 200
    res = c.esperar(id_, "generado")["resultado"]
    assert res["verificacion"]["todo_ok"] and res["n_manuales"] == 2
    nombres = {a["nombre"] for a in res["archivos"]}
    assert "mi planilla ñandú_CORREGIDA.xlsx" in nombres
    estado, datos_zip, resp = c.pedir("GET", f"/api/sesiones/{id_}/zip", crudo=True)
    assert estado == 200 and "attachment" in resp.getheader("Content-Disposition")
    assert set(zipfile.ZipFile(io.BytesIO(datos_zip)).namelist()) == nombres
    estado, _, resp = c.pedir("GET", f"/api/sesiones/{id_}/archivos/mi%20planilla%20%C3%B1and%C3%BA_CORREGIDA.xlsx", crudo=True)
    assert estado == 200 and "filename*=UTF-8''" in resp.getheader("Content-Disposition")

    sesion = json.loads((app.sesiones[id_].carpeta / "sesion.json").read_text(encoding="utf-8"))
    assert len(sesion["decisiones"]) == 2 and sesion["indice_despues"] > sesion["indice_antes"]
    _, recientes = c.pedir("GET", "/api/recientes")
    assert recientes[0]["archivo"] == "mi planilla ñandú.xlsx" and recientes[0]["decisiones"] == 2


def test_retoma_las_decisiones_del_mismo_archivo(tmp_path):
    carpeta = tmp_path / "Auditor XPE"
    ruta = crear_planilla_demo(tmp_path / "p.xlsx")
    for vuelta in range(2):
        app = Aplicacion(carpeta, con_libreoffice=False)  # como si se cerrara y abriera el programa
        srv = crear_servidor(app, 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        c = Cliente(srv.server_address[1])
        _, r = c.pedir("POST", "/api/auditar", ruta.read_bytes(), {"X-Archivo": "p.xlsx"})
        c.esperar(r["id"], "listo")
        _, datos = c.pedir("GET", f"/api/sesiones/{r['id']}?datos=1")
        datos = datos["datos"]
        if vuelta == 0:
            r06 = next(h for h in datos["hallazgos"] if h["regla"] == "R06")
            c.pedir("PUT", f"/api/sesiones/{r['id']}/decisiones/{r06['id']}", {"tipo": "accion", "indice": 0})
        else:
            assert datos["restauradas"] == 1 and len(datos["decisiones"]) == 1
            assert len(list(carpeta.iterdir())) == 1  # misma carpeta de trabajo
        srv.shutdown()
        srv.server_close()


def test_archivo_que_no_es_planilla(servidor):
    _, c = servidor
    _, r = c.pedir("POST", "/api/auditar", b"hola, no soy un excel", {"X-Archivo": "notas.xlsx"})
    estado = c.pedir("GET", f"/api/sesiones/{r['id']}")[1]
    while estado["estado"] == "analizando":
        time.sleep(0.1)
        estado = c.pedir("GET", f"/api/sesiones/{r['id']}")[1]
    assert estado["estado"] == "error" and "no es una planilla" in estado["error"]


def test_autoprueba(tmp_path):
    assert autoprueba(tmp_path / "autoprueba.log") == 0
    assert "AUTOPRUEBA OK" in (tmp_path / "autoprueba.log").read_text(encoding="utf-8")
