"""Versión web (Pyodide): el mismo enrutador sin servidor ni hilos, y los archivos que publica GitHub Pages."""

from __future__ import annotations

import importlib.util
import io
import json
import re
import zipfile
from pathlib import Path

from auditor.app import web
from auditor.demo import crear_planilla_demo

RAIZ = Path(__file__).resolve().parent.parent


def _construir_web():
    spec = importlib.util.spec_from_file_location("construir_web", RAIZ / "empaquetado" / "construir_web.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_pagina_web_al_dia():
    # web/index.html y web/manifiesto.json se generan de la interfaz y del código del programa
    assert _construir_web().main(["--comprobar"]) == 0, "corre python empaquetado/construir_web.py"
    archivos = json.loads((RAIZ / "web" / "manifiesto.json").read_text(encoding="utf-8"))["archivos"]
    assert "auditor/app/web.py" in archivos and "config.yaml" in archivos
    assert all((RAIZ / a).is_file() for a in archivos)
    # los paquetes que carga el navegador van junto a la página
    for rueda in re.findall(r'"([\w.-]+\.whl)"', (RAIZ / "web" / "trabajador.js").read_text(encoding="utf-8")):
        assert (RAIZ / "web" / "paquetes" / rueda).is_file(), rueda


def _pedir(metodo, ruta, cuerpo=None, cabeceras=None):
    if isinstance(cuerpo, dict):
        cuerpo = json.dumps(cuerpo).encode()
    estado, tipo, descarga, datos, ocupado = web.pedir(metodo, ruta, json.dumps(cabeceras or {}), cuerpo)
    if tipo.startswith("application/json"):
        datos = json.loads(datos.decode("utf-8"))
    return estado, datos, descarga, ocupado


def test_flujo_sin_hilos(tmp_path):
    avisos = []
    web.iniciar(str(tmp_path / "Auditor XPE"), None, lambda texto: avisos.append(json.loads(texto)))
    assert _pedir("GET", "/api/info")[1]["web"] is True

    ruta = crear_planilla_demo(tmp_path / "p.xlsx")
    estado, r, _, ocupado = _pedir("POST", "/api/auditar", ruta.read_bytes(), {"X-Archivo": "p.xlsx"})
    assert estado == 200 and ocupado  # el análisis queda en cola hasta trabajar()
    id_ = r["id"]
    assert _pedir("GET", f"/api/sesiones/{id_}")[1]["estado"] == "analizando"
    assert web.trabajar() and not web.trabajar()
    # cada paso se avisa al navegador, para que el monitor avance mientras Python trabaja
    estados = [a["resumen"]["estado"] for a in avisos if a["id"] == id_]
    assert len(estados) > 2 and estados[0] == "analizando" and estados[-1] == "listo"

    datos = _pedir("GET", f"/api/sesiones/{id_}?datos=1")[1]["datos"]
    r06 = next(h for h in datos["hallazgos"] if h["regla"] == "R06")
    assert _pedir("PUT", f"/api/sesiones/{id_}/decisiones/{r06['id']}", {"tipo": "valor", "texto": "x"})[0] == 400
    assert _pedir("PUT", f"/api/sesiones/{id_}/decisiones/{r06['id']}", {"tipo": "accion", "indice": 1})[0] == 200

    estado, _, _, ocupado = _pedir("POST", f"/api/sesiones/{id_}/generar", {})
    assert estado == 200 and ocupado
    web.trabajar()
    res = _pedir("GET", f"/api/sesiones/{id_}")[1]["resultado"]
    assert res["verificacion"]["todo_ok"] and res["n_manuales"] == 1
    estado, datos_zip, descarga, _ = _pedir("GET", f"/api/sesiones/{id_}/zip")
    assert estado == 200 and descarga == "auditoria_p.zip"
    assert set(zipfile.ZipFile(io.BytesIO(datos_zip)).namelist()) == {a["nombre"] for a in res["archivos"]}
    assert _pedir("GET", "/api/recientes")[1][0]["decisiones"] == 1
    assert _pedir("GET", "/api/sesiones/no-existe")[0] == 404
