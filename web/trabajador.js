// Motor del Auditor XPE en el navegador: Python (Pyodide) con el mismo código del programa de
// escritorio, en un Web Worker para que la página no se congele mientras analiza. La planilla
// no sale del navegador; las revisiones se guardan en IndexedDB.
"use strict";

const PYODIDE = new URL(self.location.href).searchParams.get("pyodide") || "https://cdn.jsdelivr.net/pyodide/v0.28.3/full/";
importScripts(PYODIDE + "pyodide.js");

const avisar = (m) => postMessage(typeof m === "string" ? m : JSON.stringify(m));
let pyodide, motor;

const sincronizar = (cargar) => new Promise((ok) => pyodide.FS.syncfs(cargar, () => ok()));

async function cargar() {
  avisar({ tipo: "cargando", texto: "Cargando Python…", avance: 0.08 });
  pyodide = await loadPyodide({ indexURL: PYODIDE });
  avisar({ tipo: "cargando", texto: "Cargando pandas, lxml y openpyxl…", avance: 0.35 });
  await pyodide.loadPackage(["pandas", "pyyaml", "lxml"]);
  // openpyxl no viene en Pyodide: va junto a la página (Python puro), sin depender de PyPI
  const paquetes = ["openpyxl-3.1.5-py2.py3-none-any.whl", "et_xmlfile-2.0.0-py3-none-any.whl"];
  await pyodide.loadPackage(paquetes.map((p) => new URL("paquetes/" + p, self.location.href).href));
  avisar({ tipo: "cargando", texto: "Cargando el auditor…", avance: 0.85 });
  const raiz = new URL("../", self.location.href);
  const manifiesto = await (await fetch(new URL("web/manifiesto.json", raiz))).json();
  for (const ruta of manifiesto.archivos) {
    const r = await fetch(new URL(ruta, raiz));
    if (!r.ok) throw new Error(`No se pudo cargar ${ruta} (${r.status})`);
    const destino = "/app/" + ruta;
    pyodide.FS.mkdirTree(destino.slice(0, destino.lastIndexOf("/")));
    pyodide.FS.writeFile(destino, await r.text());
  }
  pyodide.FS.mkdirTree("/datos");
  pyodide.FS.mount(pyodide.FS.filesystems.IDBFS, {}, "/datos");
  await sincronizar(true);
  pyodide.runPython('import sys; sys.path.insert(0, "/app")');
  motor = pyodide.pyimport("auditor.app.web");
  motor.iniciar("/datos/Auditor XPE", "/app/config.yaml", avisar);
  avisar({ tipo: "listo" });
}

const listo = cargar().catch((e) => {
  avisar({ tipo: "error-carga", texto: String((e && e.message) || e) });
  throw e;
});

async function atender(m) {
  await listo;
  let respuesta;
  try {
    const r = motor.pedir(m.metodo, m.ruta, JSON.stringify(m.cabeceras || {}), m.cuerpo || null);
    const [estado, tipo, descarga, cuerpo, ocupado] = r.toJs();
    r.destroy();
    respuesta = { tipo: "respuesta", n: m.n, estado, tipoContenido: tipo, descarga, cuerpo, ocupado };
  } catch (e) {
    const cuerpo = new TextEncoder().encode(JSON.stringify({ error: String((e && e.message) || e) }));
    respuesta = { tipo: "respuesta", n: m.n, estado: 500, tipoContenido: "application/json", cuerpo, ocupado: false };
  }
  postMessage(respuesta, [respuesta.cuerpo.buffer]);
  if (respuesta.ocupado) {
    try {
      motor.trabajar(); // análisis o generación: avisa cada paso mientras tanto
    } finally {
      avisar({ tipo: "libre" });
    }
  }
  if (m.metodo !== "GET" || respuesta.ocupado) await sincronizar(false);
}

// Un pedido a la vez, en orden
let cola = Promise.resolve();
self.onmessage = (ev) => {
  cola = cola.then(() => atender(ev.data)).catch((e) => console.error(e));
};
