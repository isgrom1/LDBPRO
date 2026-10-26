"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const normalizar = (s) => String(s ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
const fmt = (n) => Number(n).toLocaleString("es-CL", { maximumFractionDigits: 1 });
const NIVEL = {
  3: { nombre: "Críticos" },
  2: { nombre: "Advertencias" },
  1: { nombre: "Info" },
};
const PESTANAS = [
  ["pendientes", "Para revisar"],
  ["auto", "Corrección automática"],
  ["decididos", "Ya decididos"],
  ["todos", "Todos"],
];
const LIMITE_GRUPO = 40;
// Punto de color por severidad (en vez de emojis): rojo crítico, naranjo advertencia, amarillo info
const punto = (n) => `<i class="punto n${n}" aria-label="${NIVEL[n].nombre}"></i>`;

const E = {
  sesion: null,
  datos: null,
  decisiones: {},
  proyeccion: null,
  vista: "pendientes",
  agrupar: "estacion",
  niveles: new Set([3, 2, 1]),
  buscar: "",
  cerrados: new Set(),
  limites: {},
  abiertos: new Set(),
  resultado: null,
  temporizador: null,
};

// Versión web (web/motor.js): el motor corre en el navegador y no hay servidor local
const WEB = window.MotorWeb || null;

// ------------------------------------------------------------------ utilidades
async function api(metodo, ruta, cuerpo) {
  if (WEB) return WEB.pedir(metodo, ruta, cuerpo);
  const opciones = { method: metodo, headers: { "X-Auditor": "1" } };
  if (cuerpo instanceof Blob) {
    opciones.body = cuerpo;
    opciones.headers["X-Archivo"] = encodeURIComponent(cuerpo.name || "planilla.xlsx");
  } else if (cuerpo !== undefined) {
    opciones.body = JSON.stringify(cuerpo);
    opciones.headers["Content-Type"] = "application/json";
  }
  let r;
  try {
    r = await fetch(ruta, opciones);
  } catch (e) {
    throw new Error("Se perdió la conexión con el Auditor XPE. Si cerraste el programa, vuelve a abrirlo; tus decisiones quedaron guardadas.");
  }
  const datos = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(datos.error || `Error ${r.status}`);
  return datos;
}

function aviso(texto, error = false) {
  const div = document.createElement("div");
  div.className = "aviso" + (error ? " error" : "");
  div.textContent = texto;
  $("#avisos").append(div);
  setTimeout(() => div.remove(), error ? 7000 : 3500);
}

const espera = (ms) => new Promise((r) => setTimeout(r, ms));

function mostrarVista(nombre) {
  $$(".vista").forEach((v) => (v.hidden = v.id !== `vista-${nombre}`));
  const orden = ["inicio", "revision", "final"];
  const actual = orden.indexOf(nombre === "progreso" ? "inicio" : nombre);
  $$(".pasos li").forEach((li) => {
    const i = orden.indexOf(li.dataset.paso);
    li.classList.toggle("activo", i === actual);
    li.classList.toggle("hecho", i < actual);
  });
  window.scrollTo({ top: 0 });
}

function claseEstado(estado) {
  if (String(estado).startsWith("✅")) return "e-verde";
  if (String(estado).startsWith("⚠")) return "e-naranjo";
  return "e-rojo";
}

// ------------------------------------------------------------------ inicio
async function cargarInfo() {
  try {
    const info = await api("GET", "/api/info");
    if (info.web) {
      $("#pie-info").innerHTML = `Las revisiones quedan guardadas en este navegador; la planilla no se sube a internet.
        <span class="version">· v${esc(info.version)}</span>`;
      return;
    }
    $("#pie-info").innerHTML = `Los resultados se guardan en <code>${esc(info.carpeta)}</code>
      <button class="enlace" id="btn-carpeta-base">Abrir carpeta</button> <span class="version">· v${esc(info.version)}</span>`;
    $("#btn-carpeta-base").addEventListener("click", () => api("POST", "/api/abrir-carpeta").catch((e) => aviso(e.message, true)));
    document.body.classList.toggle("escritorio", !!info.escritorio);
  } catch (e) {
    /* sin información no pasa nada */
  }
}

// Los botones se conectan antes de cualquier consulta al servidor: si la primera tarda,
// la interfaz igual responde.
function iniciar() {
  document.body.classList.toggle("web", !!WEB);
  mostrarVista("inicio");
  const zona = $("#zona-carga");
  $("#archivo").addEventListener("change", (ev) => ev.target.files[0] && subir(ev.target.files[0]));
  zona.addEventListener("keydown", (ev) => (ev.key === "Enter" || ev.key === " ") && $("#archivo").click());
  ["dragenter", "dragover"].forEach((t) =>
    window.addEventListener(t, (ev) => {
      ev.preventDefault();
      zona.classList.add("encima");
    })
  );
  ["dragleave", "drop"].forEach((t) => window.addEventListener(t, () => zona.classList.remove("encima")));
  window.addEventListener("drop", (ev) => {
    ev.preventDefault();
    const archivo = ev.dataTransfer.files[0];
    if (archivo && !$("#vista-inicio").hidden) subir(archivo);
  });
  $("#btn-ejemplo").addEventListener("click", probarEjemplo);
  $("#btn-otra").addEventListener("click", volverAlInicio);
  $("#btn-otra-final").addEventListener("click", volverAlInicio);
  $("#btn-volver").addEventListener("click", () => mostrarVista("revision"));
  $("#btn-generar").addEventListener("click", generar);
  $("#btn-carpeta").addEventListener("click", abrirCarpeta);
  $("#btn-salir").addEventListener("click", salir);
  $("#r-buscar").addEventListener("input", (ev) => {
    E.buscar = normalizar(ev.target.value);
    renderLista();
  });
  $("#r-agrupar").addEventListener("click", (ev) => {
    const b = ev.target.closest("button[data-agrupar]");
    if (!b) return;
    E.agrupar = b.dataset.agrupar;
    $$("#r-agrupar button").forEach((x) => x.classList.toggle("activo", x === b));
    renderLista();
  });
  $("#r-pestanas").addEventListener("click", (ev) => {
    const b = ev.target.closest("button[data-vista]");
    if (!b) return;
    E.vista = b.dataset.vista;
    renderHerramientas();
    renderLista();
  });
  $("#r-niveles").addEventListener("click", (ev) => {
    const b = ev.target.closest("button[data-nivel]");
    if (!b) return;
    const n = Number(b.dataset.nivel);
    E.niveles.has(n) ? E.niveles.delete(n) : E.niveles.add(n);
    renderHerramientas();
    renderLista();
  });
  $("#r-lista").addEventListener("click", alHacerClic);
  $("#r-lista").addEventListener("change", alCambiar);
  $("#r-lista").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && ev.target.matches("[data-entrada], [data-nota]")) {
      const tarjeta = ev.target.closest(".hallazgo");
      guardarValor(tarjeta);
    }
  });
  $("#r-avisos").addEventListener("click", async (ev) => {
    const origen = ev.target.closest("[data-origen] button");
    if (origen) return elegirOrigen(origen);
    if (!ev.target.matches("[data-accion=reiniciar]")) return;
    if (!confirm("¿Borrar todas las decisiones guardadas y empezar de cero?")) return;
    await api("POST", `/api/sesiones/${E.sesion}/reiniciar`);
    await cargarDatos();
  });
  setInterval(() => api("POST", "/api/ping").catch(() => {}), 30000);
  api("POST", "/api/ping").catch(() => {});
  cargarInfo();
  cargarRecientes();
  document.body.dataset.listo = "1";
}

async function cargarRecientes() {
  try {
    const lista = await api("GET", "/api/recientes");
    $("#recientes").hidden = !lista.length;
    $("#lista-recientes").innerHTML = lista
      .map(
        (r) => `<button class="reciente" data-carpeta="${esc(r.carpeta)}">
          <b>${esc(r.archivo)}</b>
          <small>${esc((r.actualizado || "").replace("T", " ").slice(0, 16))} · ${esc(r.estado || "")}
          ${r.indice != null ? ` · índice ${fmt(r.indice)}` : ""} · ${r.decisiones} decisiones</small>
        </button>`
      )
      .join("");
    $$("#lista-recientes .reciente").forEach((b) =>
      b.addEventListener("click", async () => {
        mostrarProgreso(b.querySelector("b").textContent);
        try {
          const r = await api("POST", "/api/abrir", { carpeta: b.dataset.carpeta });
          await seguir(r.id);
        } catch (e) {
          volverAlInicio(e.message);
        }
      })
    );
  } catch (e) {
    $("#recientes").hidden = true;
  }
}

function volverAlInicio(error) {
  $("#progreso-global").hidden = true;
  $("#archivo").value = "";
  const caja = $("#error-inicio");
  caja.hidden = !(typeof error === "string" && error);
  caja.textContent = typeof error === "string" ? error : "";
  cargarRecientes();
  mostrarVista("inicio");
}

// ------------------------------------------------------------------ monitor de progreso
// Avance que representa cada paso que informa el servidor; entre pasos la barra sigue avanzando suave.
const ETAPAS = [
  ["Subiendo", 0.08], ["Formato real", 0.16], ["Convirtiendo", 0.26], ["Leyendo", 0.36],
  ["Revisando las reglas", 0.6], ["Calculando el puntaje", 0.84],
  ["Escribiendo la planilla corregida", 0.22], ["Verificando", 0.5], ["Escribiendo el archivo de revisión", 0.82],
  ["Listo", 1],
];
const BARRAS_PROGRESO = ["#progreso-global div", "#progreso-barra", "#modal-barra"];
const M = { inicio: 0, tiempos: [], avance: 0 };

const reloj = (ms) => {
  const s = Math.max(0, ms) / 1000;
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${(s % 60).toFixed(1).padStart(4, "0")}`;
};

function fijarAvance(fraccion) {
  M.avance = Math.max(M.avance, Math.min(1, fraccion));
  BARRAS_PROGRESO.forEach((s) => {
    const el = $(s);
    if (el) el.style.width = `${(M.avance * 100).toFixed(1)}%`;
  });
  BARRAS_PROGRESO.forEach((s) => $(s)?.parentElement.classList.toggle("completo", M.avance >= 1));
}

function iniciarMonitor(consola) {
  M.inicio = performance.now();
  M.tiempos = [];
  M.avance = 0;
  consola.innerHTML = "";
  BARRAS_PROGRESO.forEach((s) => {
    const el = $(s);
    if (el) {
      el.style.transition = "none";
      el.style.width = "0";
      el.offsetWidth; // aplica el 0 sin animar antes de volver a avanzar
      el.style.transition = "";
    }
  });
  $("#progreso-global").hidden = false;
  fijarAvance(0.04);
}

function terminarMonitor() {
  fijarAvance(1);
  setTimeout(() => ($("#progreso-global").hidden = true), 700);
}

function avanzarSegun(pasos) {
  const ultimo = pasos[pasos.length - 1] || "";
  const etapa = ETAPAS.find(([t]) => ultimo.startsWith(t));
  const base = etapa ? etapa[1] : M.avance;
  fijarAvance(Math.max(base, Math.min(M.avance + 0.012, base + 0.12, 0.96)));
}

// Consola: agrega solo las líneas nuevas (sin repetir animaciones) y marca la que está en curso
function renderConsola(el, pasos, terminado = false) {
  pasos.forEach((p, i) => {
    if (M.tiempos[i] == null) M.tiempos[i] = performance.now() - M.inicio;
    if (i >= el.children.length) {
      const li = document.createElement("li");
      li.innerHTML = `<span class="t">${reloj(M.tiempos[i])}</span><span class="msg">${esc(p)}</span><span class="estado-linea"></span>`;
      el.append(li);
    }
  });
  [...el.children].forEach((li, i) => {
    const enCurso = i === pasos.length - 1 && !terminado && pasos[i] !== "Listo";
    const estado = li.querySelector(".estado-linea");
    estado.className = `estado-linea ${enCurso ? "corriendo" : "ok"}`;
    estado.textContent = enCurso ? "EN CURSO" : "OK";
    li.querySelector(".msg").classList.toggle("con-cursor", enCurso);
  });
  el.scrollTop = el.scrollHeight;
}

function mostrarProgreso(nombre) {
  $("#progreso-titulo").textContent = "Revisando la planilla…";
  $("#progreso-archivo").textContent = nombre;
  iniciarMonitor($("#progreso-pasos"));
  renderConsola($("#progreso-pasos"), ["Subiendo el archivo"]);
  mostrarVista("progreso");
}

async function subir(archivo) {
  if (!/\.(xlsx|xlsb|xls|xlsm)$/i.test(archivo.name)) {
    volverAlInicio(`«${archivo.name}» no parece una planilla Excel (.xlsx o .xlsb).`);
    return;
  }
  if (WEB && /\.(xlsb|xls)$/i.test(archivo.name)) {
    // en el navegador no hay LibreOffice para convertirla
    volverAlInicio(`«${archivo.name}» es un .xlsb o .xls, y la versión web solo abre .xlsx. Ábrelo en Excel → Archivo → Guardar como → «Libro de Excel (.xlsx)» y súbelo de nuevo.`);
    return;
  }
  mostrarProgreso(archivo.name);
  try {
    const r = await api("POST", "/api/auditar", archivo);
    await seguir(r.id);
  } catch (e) {
    volverAlInicio(e.message);
  }
}

async function probarEjemplo() {
  mostrarProgreso("planilla_ejemplo.xlsx (datos ficticios)");
  try {
    const r = await api("POST", "/api/ejemplo");
    await seguir(r.id);
  } catch (e) {
    volverAlInicio(e.message);
  }
}

async function seguir(id) {
  E.sesion = id;
  for (;;) {
    const r = await api("GET", `/api/sesiones/${id}`);
    const pasos = ["Subiendo el archivo", ...r.pasos];
    renderConsola($("#progreso-pasos"), pasos, r.estado !== "analizando");
    avanzarSegun(pasos);
    if (r.estado === "error") throw new Error(r.error || "No se pudo revisar la planilla");
    if (r.estado !== "analizando") break;
    await espera(500);
  }
  terminarMonitor();
  await espera(350);
  await cargarDatos();
}

async function cargarDatos() {
  const r = await api("GET", `/api/sesiones/${E.sesion}?datos=1`);
  E.datos = r.datos;
  E.decisiones = r.datos.decisiones || {};
  E.carpeta = r.carpeta;
  E.proyeccion = { puntaje: r.datos.puntaje_auto, pendientes: null };
  E.abiertos.clear();
  E.limites = {};
  E.vista = "pendientes";
  $("#dl-especies").innerHTML = r.datos.nombres_bd.map((n) => `<option value="${esc(n)}">`).join("");
  renderRevision();
  mostrarVista("revision");
  actualizarProyeccion(true);
}

// ------------------------------------------------------------------ revisión
function estadoDe(h) {
  const d = E.decisiones[h.id];
  if (d) return d.tipo === "sin_auto" ? "rechazada" : "decidido";
  return h.auto !== null ? "auto" : "pendiente";
}

function enVista(h) {
  const est = estadoDe(h);
  switch (E.vista) {
    case "pendientes":
      return est === "pendiente";
    case "auto":
      return h.auto !== null;
    case "decididos":
      return est === "decidido" || est === "rechazada";
    default:
      return true;
  }
}

function coincideBusqueda(h) {
  if (!E.buscar) return true;
  return normalizar([h.regla, h.titulo, h.estacion, h.hoja, h.celda, h.valor, h.problema, h.contexto].join(" ")).includes(E.buscar);
}

function renderRevision() {
  const d = E.datos;
  $("#r-archivo").textContent = d.archivo;
  const hojas = d.hojas_registros.map((x) => `${x.n} en ${x.hoja}`).join(", ");
  $("#r-resumen").textContent = `${d.n_estaciones} estaciones · registros: ${hojas} · formato ${d.formato}`;
  const avisos = [];
  if (d.restauradas) {
    avisos.push(`<div class="alerta alerta-info">Retomaste una revisión anterior de este mismo archivo:
      se recuperaron <b>${d.restauradas}</b> decisiones. <button class="enlace" data-accion="reiniciar">Empezar de cero</button></div>`);
  }
  d.avisos.forEach((a) => avisos.push(`<div class="alerta alerta-info">⚠️ ${esc(a)}</div>`));
  avisos.push(htmlOrigen(d.origen));
  avisos.push(htmlPlantilla(d.plantilla));
  $("#r-avisos").innerHTML = avisos.join("");
  renderIndicadores();
  renderPuntaje();
  renderHerramientas();
  renderLista();
}

// Domésticas y exóticas: qué hacer con ellas y si entran en la base para PAST/R
const GRUPOS_ORIGEN = {
  domesticas: { titulo: "Domésticas", icono: "🐄", motivo: "doméstica" },
  exoticas: { titulo: "Exóticas", icono: "🐇", motivo: "exótica" },
};

function htmlOrigen(origen) {
  if (!origen || !Object.values(origen.especies).some((l) => l.length)) return "";
  const grupos = Object.entries(GRUPOS_ORIGEN)
    .filter(([g]) => origen.especies[g].length)
    .map(([g, info]) => {
      const especies = origen.especies[g]
        .map((e) => `<span class="origen-especie" title="${esc(e.nombre_cientifico)} · ${esc(e.estaciones.join(", "))}">
          ${esc(e.nombre_comun)} <small>${e.registros} reg.</small></span>`)
        .join("");
      const fuera = origen.excluir[g];
      return `<div class="origen-grupo">
        <div class="origen-especies"><b>${info.icono} ${info.titulo}</b>${especies}</div>
        <div class="segmentado" data-origen="${g}" role="group" aria-label="${info.titulo} en el análisis">
          <button data-excluir="1" class="${fuera ? "activo" : ""}">Fuera del análisis</button>
          <button data-excluir="0" class="${fuera ? "" : "activo"}">Dentro del análisis</button>
        </div>
      </div>`;
    })
    .join("");
  return `<div class="alerta alerta-origen">
    <div class="origen-titulo">Especies domésticas y exóticas en esta planilla</div>
    <p><b>En la planilla que entregas, déjalas tal cual:</b> se entregan todos los registros de terreno y el
      auditor no borra ninguno.</p>
    <p class="tenue">Para la lista de especies y los análisis, la LdB de XPE deja fuera el ganado, los perros y los
      gatos por ser domésticos, y sí incluye las exóticas (conejo, liebre, gorrión, codorniz…), marcadas como «Exótico».
      Ya viene elegido así; cámbialo solo si tu proyecto usa otro criterio.</p>
    ${grupos}
    <p class="tenue origen-nota">Esto solo cambia <code>base_limpia_larga.csv</code> (la tabla para PAST o R): las filas no
      se borran, quedan marcadas en las columnas <code>origen</code> y <code>excluir_motivo</code>.</p>
  </div>`;
}

// B01: la lista de especies viene en la plantilla de XPE; se avisa una vez, sin tarjetas
function htmlPlantilla(items) {
  if (!items || !items.length) return "";
  const tipos = [
    ["veces en nombres BD", "nombres comunes repetidos con especies distintas (p. ej. «Sapo de monte»)"],
    ["La clase", "clases en latín (Mammalia, Reptilia) en vez de Mamíferos, Reptiles"],
    ["", "nombres con espacios de más"],
  ];
  const cuenta = tipos.map(([clave, texto]) => {
    const n = items.filter((i) => (clave ? i.problema.includes(clave) : !tipos.some(([c]) => c && i.problema.includes(c)))).length;
    return n ? `<li><b>${n}</b> ${texto}</li>` : "";
  });
  const usados = E.datos.hallazgos.filter((h) => h.regla === "R27").length;
  const efecto = usados
    ? `En esta planilla, <b>${usados}</b> registro${usados === 1 ? "" : "s"} usa${usados === 1 ? "" : "n"} uno de los nombres repetidos: salen marcados con R27 para que confirmes la especie.`
    : "En esta planilla no usaste ninguno de los nombres repetidos, así que no te afecta.";
  return `<div class="alerta alerta-origen">
    <div class="origen-titulo">Lista de especies de la plantilla («nombres BD»)</div>
    <p class="tenue">Viene en la plantilla de XPE: no es un error de tu planilla, no resta puntaje y el auditor no la cambia. Tiene:</p>
    <ul class="plantilla-lista">${cuenta.join("")}</ul>
    <p>${efecto}</p>
    <details><summary>Ver el detalle</summary><ul class="plantilla-detalle">
      ${items.map((i) => `<li><code>${esc(i.celda || "")}</code> ${esc(i.problema)}</li>`).join("")}
    </ul></details>
  </div>`;
}

async function elegirOrigen(boton) {
  const grupo = boton.closest("[data-origen]").dataset.origen;
  const excluir = boton.dataset.excluir === "1";
  if (E.datos.origen.excluir[grupo] === excluir) return;
  try {
    const r = await api("POST", `/api/sesiones/${E.sesion}/origen`, { [grupo]: excluir });
    E.datos.origen.excluir = r.excluir;
    renderRevision();
    aviso(`${GRUPOS_ORIGEN[grupo].titulo}: ${excluir ? "fuera del" : "dentro del"} análisis. Se aplica al generar.`);
  } catch (e) {
    aviso(e.message, true);
  }
}

function renderIndicadores() {
  const d = E.datos;
  const filas = [];
  if (d.aves) {
    filas.push(`<div class="indicador"><span class="tag">AVES</span><div><b>${fmt(d.aves.pct)}%</b> de los registros de aves en transecto
      fue entre ${esc(d.aves.rango)} (${d.aves.n_en_rango} de ${d.aves.n}).</div></div>`);
  }
  const presencias = Object.entries(d.presencias || {});
  if (presencias.length) {
    const lista = presencias.map(([e, ests]) => `${esc(e)} (${esc(ests.join(", "))})`).join("; ");
    filas.push(`<div class="indicador"><span class="tag">P</span><div>Registros de presencia «P»: <b>${lista}</b>.
      Cuentan para la riqueza, no para abundancia ni densidad.</div></div>`);
  }
  if (d.sin_registros.length) {
    filas.push(`<div class="indicador"><span class="tag">0 REG</span><div>Estaciones sin registros: <b>${esc(d.sin_registros.join(", "))}</b>.
      No se borran: cuentan como 0.</div></div>`);
  }
  d.hojas_sin_datos.forEach((h) => filas.push(`<div class="indicador"><span class="tag">HOJA</span><div>«${esc(h)}» no tiene datos: no se revisó.</div></div>`));
  filas.push(`<div class="indicador"><span class="tag">AUTO</span><div><b>${d.n_cambios_auto}</b> celdas se pueden corregir solas
    (espacios invisibles, mayúsculas, «Si» → 1…).</div></div>`);
  $("#r-indicadores").innerHTML = filas.join("");
}

function anillo(valor, estado) {
  const r = 62;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, valor)) / 100;
  const color = { "e-verde": "var(--verde)", "e-naranjo": "var(--naranjo)", "e-rojo": "var(--rojo)" }[claseEstado(estado)];
  return `<svg width="156" height="156" viewBox="0 0 150 150" aria-hidden="true">
      <circle cx="75" cy="75" r="${r}" fill="none" stroke="var(--sup-3)" stroke-width="7"/>
      <circle cx="75" cy="75" r="${r}" fill="none" stroke="${color}" stroke-width="7" stroke-linecap="round"
        stroke-dasharray="${(c * pct).toFixed(1)} ${c.toFixed(1)}"
        style="transition: stroke-dasharray .8s cubic-bezier(.25,.8,.25,1); filter: drop-shadow(0 0 6px ${color})"/>
    </svg>
    <div class="numero"><b>${fmt(valor)}</b><small>DE 100</small></div>`;
}

function renderPuntaje(cargando = false) {
  const d = E.datos;
  const p = E.proyeccion.puntaje;
  const caja = $("#r-anillo");
  caja.innerHTML = anillo(p.indice, p.estado);
  caja.classList.toggle("cargando", cargando);
  caja.title = "Puntaje proyectado con las correcciones automáticas y tus decisiones";
  const estado = $("#r-estado");
  estado.textContent = p.estado;
  estado.className = `estado ${claseEstado(p.estado)}`;
  $("#r-comparacion").innerHTML = `
    <div><span>Planilla tal como llegó</span><b>${fmt(d.puntaje_antes.indice)}</b></div>
    <div><span>Con correcciones automáticas</span><b>${fmt(d.puntaje_auto.indice)}</b></div>
    <div><span>Con tus decisiones</span><b>${fmt(p.indice)}</b></div>`;
  const barra = (titulo, pct, n, total) => `<div class="barra-pct"><div><span>${titulo}</span><b>${fmt(pct)}% (${n}/${total})</b></div>
      <div class="pista"><div class="relleno" style="width:${pct}%"></div></div></div>`;
  $("#r-barras").innerHTML =
    barra("Registros limpios", p.pct_registros, p.n_registros_limpios, p.n_registros) +
    barra("Estaciones limpias", p.pct_estaciones, p.n_estaciones_limpias, p.n_estaciones);
  renderBarraAccion();
}

function renderBarraAccion() {
  const hs = E.datos.hallazgos;
  const manuales = Object.values(E.decisiones).filter((d) => d.tipo !== "sin_auto").length;
  const auto = hs.filter((h) => h.auto !== null && !E.decisiones[h.id]).length;
  const pend = E.proyeccion.pendientes;
  const resto = pend ? ` · quedan <b>${pend[3]}</b> ${punto(3)} y <b>${pend[2]}</b> ${punto(2)}` : "";
  $("#r-barra-texto").innerHTML = `<b>${manuales}</b> decisiones tuyas · <b>${auto}</b> correcciones automáticas${resto}`;
}

function renderHerramientas() {
  const hs = E.datos.hallazgos.filter((h) => E.niveles.has(h.nivel));
  const cuenta = {};
  PESTANAS.forEach(([v]) => {
    const previo = E.vista;
    E.vista = v;
    cuenta[v] = hs.filter(enVista).length;
    E.vista = previo;
  });
  $("#r-pestanas").innerHTML = PESTANAS.map(
    ([v, t]) => `<button role="tab" data-vista="${v}" class="${E.vista === v ? "activo" : ""}">${t}<span class="cuenta">${cuenta[v]}</span></button>`
  ).join("");
  const enPestana = E.datos.hallazgos.filter(enVista);
  $("#r-niveles").innerHTML = [3, 2, 1]
    .map((n) => {
      const c = enPestana.filter((h) => h.nivel === n).length;
      return `<button class="chip ${E.niveles.has(n) ? "" : "apagado"}" data-nivel="${n}" title="Mostrar u ocultar ${NIVEL[n].nombre.toLowerCase()}">${punto(n)} ${c}</button>`;
    })
    .join("");
}

function agrupar(lista) {
  const grupos = new Map();
  const ambientes = Object.fromEntries(E.datos.estaciones.map((e) => [e.id, e.ambiente]));
  for (const h of lista) {
    let clave, titulo, sub;
    if (E.agrupar === "estacion") {
      clave = h.estacion || "~general";
      titulo = h.estacion || "General";
      sub = h.estacion ? ambientes[h.estacion] || (E.datos.estaciones.some((e) => e.id === h.estacion) ? "sin ambiente" : "no existe en Estaciones") : "Plantilla, diccionario e indicadores";
    } else {
      clave = h.regla;
      titulo = `${h.regla} · ${h.titulo}`;
      sub = "";
    }
    if (!grupos.has(clave)) grupos.set(clave, { clave, titulo, sub, items: [], nivel: 0 });
    const g = grupos.get(clave);
    g.items.push(h);
    g.nivel = Math.max(g.nivel, h.nivel);
  }
  const natural = (a, b) => a.localeCompare(b, "es", { numeric: true });
  const orden = [...grupos.values()];
  // «General» (plantilla, diccionario, indicadores) va al final
  if (E.agrupar === "estacion") orden.sort((a, b) => (a.clave === "~general") - (b.clave === "~general") || natural(a.clave, b.clave));
  else orden.sort((a, b) => b.nivel - a.nivel || natural(a.clave, b.clave));
  orden.forEach((g) => g.items.sort((a, b) => b.nivel - a.nivel || natural(a.regla, b.regla) || natural(a.celda || "", b.celda || "")));
  return orden;
}

function renderLista() {
  const lista = E.datos.hallazgos.filter((h) => enVista(h) && E.niveles.has(h.nivel) && coincideBusqueda(h));
  const cont = $("#r-lista");
  if (!lista.length) {
    const mensajes = {
      pendientes: ["✓", "No queda nada por revisar con estos filtros."],
      auto: ["0", "No hay correcciones automáticas con estos filtros."],
      decididos: ["0", "Todavía no tomas decisiones. Ve a «Para revisar»."],
      todos: ["∅", "No hay hallazgos que coincidan."],
    };
    const [icono, texto] = mensajes[E.vista];
    cont.innerHTML = `<div class="vacio"><span>${icono}</span>${texto}</div>`;
    return;
  }
  cont.innerHTML = agrupar(lista)
    .map((g) => {
      const limite = E.limites[g.clave] || LIMITE_GRUPO;
      const contadores = [3, 2, 1]
        .map((n) => {
          const c = g.items.filter((h) => h.nivel === n).length;
          return c ? `<span class="c${n}">${punto(n)} ${c}</span>` : "";
        })
        .join("");
      const mas = g.items.length > limite
        ? `<button class="btn-sec mas" data-accion="mas" data-grupo="${esc(g.clave)}">Mostrar ${g.items.length - limite} más</button>`
        : "";
      return `<section class="grupo ${E.cerrados.has(g.clave) ? "cerrado" : ""}" data-grupo="${esc(g.clave)}">
        <button class="grupo-cabecera" data-accion="plegar"><span class="flecha">▾</span><b>${esc(g.titulo)}</b>
          <span class="sub">${esc(g.sub)}</span><span class="contadores">${contadores}</span></button>
        <div class="grupo-cuerpo">${g.items.slice(0, limite).map(tarjeta).join("")}${mas}</div>
      </section>`;
    })
    .join("");
}

function placeholder(h) {
  return {
    numero: "Escribe un número",
    fecha: "dd-mm-aaaa",
    hora: "hh:mm",
    especie: "Nombre como en nombres BD",
  }[h.tipo] || "Escribe el valor correcto";
}

function tarjeta(h) {
  const d = E.decisiones[h.id];
  const resuelto = (d && d.tipo !== "sin_auto") || (h.auto !== null && !d);
  const lugar = h.celda ? `${h.hoja} · ${h.celda}` : h.hoja;
  const estacion = E.agrupar === "regla" && h.estacion ? `<span class="etiqueta">${esc(h.estacion)}</span>` : "";
  return `<article class="hallazgo n${h.nivel} ${resuelto ? "resuelto" : ""}" data-id="${h.id}">
    <div class="h-cabecera">
      <div class="h-titulo"><span class="regla">${esc(h.regla)}</span> ${esc(h.titulo)}</div>
      ${estacion}<span class="etiqueta" title="${esc(h.columna || "")}">${esc(lugar)}</span>
    </div>
    ${h.contexto ? `<div class="h-contexto">${esc(h.contexto)}</div>` : ""}
    ${h.valor ? `<div class="h-valor">Valor actual: <code>${esc(h.valor)}</code></div>` : ""}
    <div class="h-problema">${esc(h.problema)}</div>
    ${h.comparacion ? tablaComparacion(h.comparacion) : ""}
    <div class="h-sugerencia">${esc(h.sugerencia)}</div>
    ${zonaDecision(h, d)}
  </article>`;
}

// Dos filas lado a lado (p. ej. un posible duplicado): solo las columnas que cambian
function tablaComparacion([cabecera, ...filas]) {
  const celdas = (fila, tag) => fila.map((v) => `<${tag}>${esc(v)}</${tag}>`).join("");
  return `<div class="comparacion-tabla"><table>
    <thead><tr>${celdas(cabecera, "th")}</tr></thead>
    <tbody>${filas.map((f) => `<tr>${celdas(f, "td")}</tr>`).join("")}</tbody>
  </table></div>`;
}

function zonaDecision(h, d) {
  if (d && d.tipo !== "sin_auto") {
    const otraCelda = d.celda && (d.hoja !== h.hoja || d.celda !== h.celda) ? ` en ${d.hoja}!${d.celda}` : "";
    const texto = d.tipo === "ok" ? "Está bien así" : d.valor === "(vacío)" ? `Dejar vacía${otraCelda}` : `Nuevo valor: ${d.valor}${otraCelda}`;
    return `<div class="decidido"><span class="pildora p-ok">✓ ${esc(texto)}</span>
      ${d.nota ? `<span class="nota">«${esc(d.nota)}»</span>` : ""}
      <span class="derecha"><button class="btn-fantasma" data-accion="deshacer">↶ Deshacer</button></span></div>`;
  }
  let html = "";
  if (h.auto !== null) {
    html += `<div class="decidir"><label class="interruptor"><input type="checkbox" data-accion="auto" ${d ? "" : "checked"}>
        Corregir automáticamente: <code>${esc(h.valor || "(vacío)")}</code> → <code>${esc(h.auto)}</code></label>`;
    if (!E.abiertos.has(h.id)) {
      return html + `<span class="derecha"><button class="enlace" data-accion="abrir">Escribir otro valor…</button></span></div>`;
    }
    html += "</div>";
  }
  // Motivos de terreno habituales: un clic = «está bien así» con esa nota
  const partes = (h.justificaciones || []).map(
    (j, i) => `<button class="btn-accion btn-justificar" data-accion="justificar" data-indice="${i}">✓ ${esc(j)}</button>`
  );
  h.acciones.forEach((a, i) => partes.push(`<button class="btn-accion" data-accion="rapida" data-indice="${i}">${esc(a)}</button>`));
  if (h.editable) {
    if (partes.length) partes.push(`<span class="separador">o</span>`);
    if (h.opciones) {
      partes.push(`<select data-entrada aria-label="Elegir valor"><option value="">Elegir de la lista…</option>${h.opciones
        .map((o) => `<option>${esc(o)}</option>`)
        .join("")}</select>`);
    } else {
      const lista = h.tipo === "especie" ? ' list="dl-especies"' : "";
      const modo = h.tipo === "numero" ? ' inputmode="decimal"' : "";
      partes.push(`<input type="text" data-entrada placeholder="${placeholder(h)}" aria-label="Nuevo valor"${lista}${modo}>`);
    }
    partes.push(`<button class="btn-sec" data-accion="guardar">Guardar</button>`);
  } else if (!h.acciones.length && !(h.justificaciones || []).length) {
    const motivos = {
      sin_edicion: "No hace falta cambiar ningún valor: revísalo y, si está correcto, márcalo «Está bien así».",
      formula: "Es una celda con fórmula (columna naranja): corrige en Excel el dato del que depende.",
      general: "Es un problema general: revísalo en la planilla o en la plantilla.",
    };
    partes.push(`<span class="solo-excel">${motivos[h.motivo] || motivos.general}</span>`);
  }
  return html + `<div class="decidir">${partes.join(" ")}
      <span class="derecha"><button class="enlace" data-accion="nota">+ nota</button>
      <button class="btn-fantasma" data-accion="ok" title="No cambiar nada y no contarlo en el puntaje">✓ Está bien así</button></span>
      <input type="text" class="nota-entrada" data-nota placeholder="Nota (opcional): por qué lo decides así" hidden>
    </div>`;
}

function hallazgoDe(tarjeta) {
  return E.datos.hallazgos.find((h) => h.id === tarjeta.dataset.id);
}

function redibujar(id) {
  const h = E.datos.hallazgos.find((x) => x.id === id);
  const viejo = $(`.hallazgo[data-id="${id}"]`);
  if (viejo && h) viejo.outerHTML = tarjeta(h);
  renderHerramientas();
  renderBarraAccion();
}

async function decidir(tarjeta, datos) {
  const h = hallazgoDe(tarjeta);
  const nota = $("[data-nota]", tarjeta)?.value.trim();
  if (nota) datos.nota = nota;
  try {
    const r = await api("PUT", `/api/sesiones/${E.sesion}/decisiones/${h.id}`, datos);
    E.decisiones[h.id] = r.decision;
    E.abiertos.delete(h.id);
    redibujar(h.id);
    actualizarProyeccion();
  } catch (e) {
    aviso(e.message, true);
    redibujar(h.id);
  }
}

async function deshacer(tarjeta) {
  const h = hallazgoDe(tarjeta);
  try {
    await api("DELETE", `/api/sesiones/${E.sesion}/decisiones/${h.id}`);
    delete E.decisiones[h.id];
    redibujar(h.id);
    actualizarProyeccion();
  } catch (e) {
    aviso(e.message, true);
  }
}

function guardarValor(tarjeta) {
  const entrada = $("[data-entrada]", tarjeta);
  if (!entrada) return;
  const texto = entrada.value.trim();
  if (!texto) {
    aviso(entrada.tagName === "SELECT" ? "Elige un valor de la lista." : "Escribe el valor nuevo.");
    entrada.focus();
    return;
  }
  decidir(tarjeta, { tipo: "valor", texto });
}

function alHacerClic(ev) {
  const b = ev.target.closest("[data-accion]");
  if (!b || b.matches("input[type=checkbox]")) return;
  const accion = b.dataset.accion;
  if (accion === "plegar") {
    const g = b.closest(".grupo");
    g.classList.toggle("cerrado");
    g.classList.contains("cerrado") ? E.cerrados.add(g.dataset.grupo) : E.cerrados.delete(g.dataset.grupo);
    return;
  }
  if (accion === "mas") {
    E.limites[b.dataset.grupo] = (E.limites[b.dataset.grupo] || LIMITE_GRUPO) + 200;
    renderLista();
    return;
  }
  const tarjeta = b.closest(".hallazgo");
  if (!tarjeta) return;
  if (accion === "rapida") decidir(tarjeta, { tipo: "accion", indice: Number(b.dataset.indice) });
  else if (accion === "guardar") guardarValor(tarjeta);
  else if (accion === "ok") decidir(tarjeta, { tipo: "ok" });
  else if (accion === "justificar") decidir(tarjeta, { tipo: "ok", nota: hallazgoDe(tarjeta).justificaciones[Number(b.dataset.indice)] });
  else if (accion === "deshacer") deshacer(tarjeta);
  else if (accion === "abrir") {
    E.abiertos.add(tarjeta.dataset.id);
    redibujar(tarjeta.dataset.id);
    $(`.hallazgo[data-id="${tarjeta.dataset.id}"] [data-entrada]`)?.focus();
  } else if (accion === "nota") {
    const nota = $("[data-nota]", tarjeta);
    nota.hidden = !nota.hidden;
    if (!nota.hidden) nota.focus();
  }
}

function alCambiar(ev) {
  if (!ev.target.matches("input[data-accion=auto]")) return;
  const tarjeta = ev.target.closest(".hallazgo");
  if (ev.target.checked) deshacer(tarjeta);
  else decidir(tarjeta, { tipo: "sin_auto" });
}

function actualizarProyeccion(inmediato = false) {
  clearTimeout(E.temporizador);
  renderPuntaje(true);
  E.temporizador = setTimeout(
    async () => {
      try {
        E.proyeccion = await api("GET", `/api/sesiones/${E.sesion}/proyeccion`);
        renderPuntaje(false);
      } catch (e) {
        aviso("No se pudo recalcular el puntaje: " + e.message, true);
        renderPuntaje(false);
      }
    },
    inmediato ? 0 : 700
  );
}

// ------------------------------------------------------------------ generar y descargar
async function generar() {
  const boton = $("#btn-generar");
  boton.disabled = true;
  $("#modal-titulo").textContent = "Generando la planilla corregida…";
  iniciarMonitor($("#modal-pasos"));
  $("#modal").hidden = false;
  try {
    await api("POST", `/api/sesiones/${E.sesion}/generar`);
    for (;;) {
      await espera(600);
      const r = await api("GET", `/api/sesiones/${E.sesion}`);
      renderConsola($("#modal-pasos"), r.pasos, r.estado === "generado");
      avanzarSegun(r.pasos);
      if (r.estado === "generado") {
        terminarMonitor();
        await espera(400);
        E.resultado = r.resultado;
        renderFinal(r);
        mostrarVista("final");
        break;
      }
      if (r.estado !== "generando") throw new Error(r.error || "No se pudo generar la planilla");
    }
  } catch (e) {
    aviso(e.message, true);
    $("#progreso-global").hidden = true;
  } finally {
    $("#modal").hidden = true;
    boton.disabled = false;
  }
}

function renderFinal(r) {
  const res = r.resultado;
  const a = res.puntaje_antes;
  const d = res.puntaje_despues;
  $("#f-subtitulo").textContent = `${E.datos.archivo} · ${res.n_cambios} celdas corregidas (${res.n_manuales} por tus decisiones)` +
    (res.aceptados ? ` · ${res.aceptados} hallazgos marcados «está bien así»` : "");
  $("#f-puntaje").innerHTML = `
    <div><small>Antes</small><span class="valor">${fmt(a.indice)}</span><span class="estado ${claseEstado(a.estado)}">${esc(a.estado)}</span></div>
    <div class="flecha">→</div>
    <div><small>Después</small><span class="valor">${fmt(d.indice)}</span><span class="estado ${claseEstado(d.estado)}">${esc(d.estado)}</span></div>`;
  const p = res.pendientes;
  $("#f-pendientes").innerHTML = p[3] + p[2]
    ? `<p class="tenue">Todavía quedan <b>${p[3]}</b> ${punto(3)} críticos y <b>${p[2]}</b> ${punto(2)} advertencias (y ${p[1]} ${punto(1)} avisos).
        Puedes volver a la revisión, decidir y generar de nuevo.</p>`
    : `<p class="tenue">No quedan problemas críticos ni advertencias.</p>`;
  const v = res.verificacion;
  const linea = (ok, texto) =>
    `<li><span class="estado-linea ${ok ? "ok" : "err"}">${ok ? "✓ OK " : "✗ ERR"}</span><span class="msg">${texto}</span></li>`;
  $("#f-verificacion").innerHTML = v
    ? [
        linea(v.listas[2], `Listas desplegables conservadas: ${v.listas[1]} de ${v.listas[0]}`),
        linea(v.formulas[2], `Fórmulas: ${v.formulas[1]} (original ${v.formulas[0]})`),
        linea(v.celdas[2], `Solo cambiaron las ${v.celdas[0]} celdas corregidas`),
        linea(v.estilos, "Formato y colores intactos"),
        linea(v.reauditoria, "La planilla corregida se volvió a revisar y coincide"),
        v.libreoffice === null ? "" : linea(v.libreoffice, "Fórmulas recalculadas sin errores nuevos (LibreOffice)"),
      ].join("")
    : `<li><span class="msg">No se generó planilla corregida.</span></li>`;
  const base = `/api/sesiones/${E.sesion}`;
  const acciones = (f) =>
    WEB
      ? `<button class="btn-sec" data-descargar="${base}/archivos/${encodeURIComponent(f.nombre)}">Descargar</button>`
      : `<button class="btn-sec" data-abrir="${esc(f.nombre)}">Abrir</button>
        <a class="btn-fantasma" href="${base}/archivos/${encodeURIComponent(f.nombre)}" download title="Guardar una copia en Descargas">Copia</a>`;
  $("#f-archivos").innerHTML = res.archivos
    .map(
      (f) => `<div class="archivo"><span class="archivo-icono">${esc(f.nombre.split(".").pop().toUpperCase())}</span>
        <div><b>${esc(f.nombre)}</b><small>${esc(f.descripcion)} · ${fmt(f.bytes / 1024)} KB</small></div>
        ${acciones(f)}</div>`
    )
    .join("");
  $("#btn-zip").href = WEB ? "#" : `${base}/zip`;
  $("#btn-zip").dataset.descargar = WEB ? `${base}/zip` : "";
  $$("[data-descargar]").forEach((b) => {
    if (!b.dataset.descargar) return;
    b.onclick = (ev) => {
      ev.preventDefault();
      WEB.descargar(b.dataset.descargar).catch((e) => aviso(e.message, true));
    };
  });
  $$("#f-archivos [data-abrir]").forEach((b) =>
    b.addEventListener("click", async () => {
      try {
        const r = await api("POST", `${base}/abrir-archivo`, { nombre: b.dataset.abrir });
        if (!r.ok) aviso("No se pudo abrir el archivo. Está en la carpeta de resultados.", true);
      } catch (e) {
        aviso(e.message, true);
      }
    })
  );
  $("#f-carpeta").textContent = WEB
    ? "También quedan guardados en este navegador: los encuentras en «Revisiones recientes»."
    : `Guardados también en: ${r.carpeta}`;
}

async function abrirCarpeta() {
  try {
    const r = await api("POST", `/api/sesiones/${E.sesion}/abrir-carpeta`);
    if (!r.ok) aviso("No se pudo abrir la carpeta automáticamente. Ruta: " + $("#f-carpeta").textContent);
  } catch (e) {
    aviso(e.message, true);
  }
}

async function salir() {
  if (!confirm("¿Cerrar el Auditor XPE? Tus decisiones quedan guardadas.")) return;
  try {
    await api("POST", "/api/salir");
  } finally {
    mostrarVista("cerrado");
  }
}

document.addEventListener("DOMContentLoaded", iniciar);
