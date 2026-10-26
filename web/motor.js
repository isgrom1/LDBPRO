// Puente entre la interfaz (auditor/app/static/app.js) y el motor que corre en el navegador
// (trabajador.js): las mismas rutas /api/… del programa de escritorio, sin servidor. Mientras el
// motor analiza o genera, el estado de la sesión se responde con lo último que avisó, para que
// el monitor siga avanzando.
"use strict";

(() => {
  const pyodide = new URLSearchParams(location.search).get("pyodide"); // para probar con una copia local
  const trabajador = new Worker("trabajador.js" + (pyodide ? "?pyodide=" + encodeURIComponent(pyodide) : ""));
  const esperando = new Map();
  const estados = new Map();
  let n = 0;
  let ocupado = false;
  let resolver;
  const listo = new Promise((ok, mal) => (resolver = { ok, mal }));
  listo.catch(() => {});

  function mostrarCarga(texto, avance, error = false) {
    const tile = document.querySelector(".tile-motor");
    const barra = document.getElementById("progreso-global");
    if (barra) {
      barra.hidden = avance >= 1 && !error;
      barra.firstElementChild.style.width = `${Math.round(Math.max(avance, 0.03) * 100)}%`;
    }
    if (!tile) return;
    if (error) {
      tile.querySelector("b").textContent = "No se pudo cargar el motor";
      tile.querySelector("p").textContent = texto;
    } else if (avance >= 1) {
      tile.querySelector("b").textContent = "46 revisiones · en tu navegador";
      tile.querySelector("p").textContent = "La planilla se revisa en tu computador: nunca se sube a internet.";
    } else {
      tile.querySelector("b").textContent = `Preparando el motor… ${Math.round(avance * 100)}%`;
      tile.querySelector("p").textContent = `${texto} La primera vez tarda un poco; después queda guardado.`;
    }
  }

  trabajador.onmessage = (ev) => {
    const m = typeof ev.data === "string" ? JSON.parse(ev.data) : ev.data;
    if (m.tipo === "cargando") return mostrarCarga(m.texto, m.avance);
    if (m.tipo === "listo") {
      mostrarCarga("", 1);
      return resolver.ok();
    }
    if (m.tipo === "error-carga") {
      mostrarCarga(`${m.texto}. Revisa tu conexión a internet y recarga la página.`, 0, true);
      return resolver.mal(new Error("No se pudo cargar el motor del auditor: " + m.texto));
    }
    if (m.tipo === "estado") return void estados.set(m.id, m.resumen);
    if (m.tipo === "libre") return void (ocupado = false);
    if (m.tipo === "respuesta") {
      if (m.ocupado) ocupado = true;
      const ok = esperando.get(m.n);
      esperando.delete(m.n);
      if (ok) ok(m);
    }
  };
  trabajador.onerror = (e) => {
    mostrarCarga(`${e.message || "error al iniciar"}. Revisa tu conexión a internet y recarga la página.`, 0, true);
    resolver.mal(new Error("No se pudo cargar el motor del auditor"));
  };

  function enviar(mensaje, transferir = []) {
    return new Promise((ok) => {
      mensaje.n = ++n;
      esperando.set(mensaje.n, ok);
      trabajador.postMessage(mensaje, transferir);
    });
  }

  const leer = (r) => (r.cuerpo && r.cuerpo.length ? JSON.parse(new TextDecoder().decode(r.cuerpo)) : {});

  async function pedir(metodo, ruta, cuerpo) {
    await listo;
    const sesion = ruta.match(/^\/api\/sesiones\/([^/?]+)$/);
    if (ocupado && metodo === "GET" && sesion && estados.has(sesion[1])) return estados.get(sesion[1]);
    const cabeceras = {};
    let bytes = null;
    if (cuerpo instanceof Blob) {
      bytes = new Uint8Array(await cuerpo.arrayBuffer());
      cabeceras["X-Archivo"] = encodeURIComponent(cuerpo.name || "planilla.xlsx");
    } else if (cuerpo !== undefined) {
      bytes = new TextEncoder().encode(JSON.stringify(cuerpo));
    }
    const r = await enviar({ metodo, ruta, cabeceras, cuerpo: bytes }, bytes ? [bytes.buffer] : []);
    const datos = leer(r);
    if (r.estado >= 400) throw new Error(datos.error || `Error ${r.estado}`);
    if (datos && datos.id && datos.estado) estados.set(datos.id, datos);
    return datos;
  }

  async function descargar(ruta) {
    await listo;
    const r = await enviar({ metodo: "GET", ruta, cabeceras: {}, cuerpo: null });
    if (r.estado >= 400) throw new Error(leer(r).error || "No se pudo descargar el archivo");
    const url = URL.createObjectURL(new Blob([r.cuerpo], { type: r.tipoContenido }));
    const a = document.createElement("a");
    a.href = url;
    a.download = r.descarga || "archivo";
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  window.MotorWeb = { pedir, descargar, listo };
})();
