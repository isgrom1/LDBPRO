"""Una revisión completa: análisis, decisiones del usuario y generación de salidas.

La línea de comandos la usa sin decisiones (solo auto-correcciones); la aplicación le suma lo
que el usuario decide en pantalla: escribir otro valor, aceptar un hallazgo «está bien así» o
rechazar una corrección automática.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .formato import convertir_con_libreoffice, detectar_formato, nombre_base
from .hallazgos import Hallazgo
from .modelo import SOBRESCRIBIBLES, Libro, Tabla, leer_libro
from .motor import (
    Cambio,
    Puntaje,
    aplicar_en_memoria,
    auditar,
    calcular_puntaje,
    cambios_por_hoja,
    consolidar_cambios,
)
from .reportes import (
    escribir_base_larga,
    escribir_cambios_csv,
    escribir_hallazgos_csv,
    escribir_reporte,
    filas_base_larga,
    resumen_origen,
)
from .revision import escribir_revision
from .texto import (
    PRESENCIA,
    a_fecha,
    a_hora,
    a_numero,
    es_presencia,
    fecha_a_excel,
    hora_a_excel,
    limpiar,
    mostrar,
    numero_para_celda,
)
from .verificacion import Verificacion, verificar_corregida
from .xlsx_xml import escribir_con_cambios

NUMERICAS = {
    "n", "abundancia", "utm_e", "utm_s", "utm_e_ini", "utm_s_ini", "utm_e_fin", "utm_s_fin",
    "utm_e_captura", "utm_s_captura", "met_transecto", "met_pb_aves", "met_pb_anfibios",
    "met_camara", "met_sherman",
}
TIPOS_DECISION = ("valor", "ok", "sin_auto")


class ErrorDecision(ValueError):
    """La decisión no se puede aplicar (celda con fórmula, valor no numérico…)."""


@dataclass
class Decision:
    tipo: str  # "valor": escribir un valor · "ok": está bien así · "sin_auto": no aplicar la auto-corrección
    valor: object = None  # valor ya convertido para la celda
    hoja: str | None = None
    celda: str | None = None
    texto: str = ""  # lo que escribió o eligió el usuario
    nota: str = ""
    reemplaza_formula: bool = False

    def a_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def de_dict(cls, d: dict) -> Decision:
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})

    def describir(self) -> str:
        if self.tipo == "valor":
            texto = f"Nuevo valor: {mostrar(self.valor)}" if self.valor is not None else "Dejar la celda vacía"
            texto += f" (en {self.hoja}!{self.celda})"
        elif self.tipo == "ok":
            texto = "Está bien así"
        else:
            texto = "No aplicar la corrección automática"
        return f"{texto}. Nota: {self.nota}" if self.nota else texto


@dataclass
class Analisis:
    ruta: Path  # archivo tal como llegó
    ruta_xlsx: Path  # .xlsx efectivamente leído (convertido si llegó como .xlsb/.xls)
    nombre: str
    formato: str
    config: dict
    libro: Libro
    ctx: object
    antes: list[Hallazgo]
    cambios_auto: list[Cambio]
    avisos_cambios: list[str]
    despues: list[Hallazgo]  # proyectado: solo auto-correcciones, en memoria
    puntaje_antes: Puntaje
    puntaje_despues: Puntaje
    por_clave: dict[str, Hallazgo] = field(default_factory=dict)

    def __post_init__(self):
        self.por_clave = {h.clave(): h for h in self.antes}


@dataclass
class Resultado:
    carpeta: Path
    nombre: str
    libro: Libro
    hallazgos_antes: list[Hallazgo]
    hallazgos_despues: list[Hallazgo]
    puntaje_antes: Puntaje
    puntaje_despues: Puntaje
    cambios: list[Cambio]
    avisos_cambios: list[str]
    verificacion: Verificacion | None
    estadisticas: dict
    archivos: dict[str, Path] = field(default_factory=dict)
    decisiones: dict[str, Decision] = field(default_factory=dict)
    aceptados: int = 0


# --------------------------------------------------------------------------- análisis


def analizar(ruta: str | Path, config: dict, carpeta_trabajo: str | Path, informar=print) -> Analisis:
    ruta = Path(ruta).resolve()
    if not ruta.is_file():
        raise FileNotFoundError(f"No existe el archivo {ruta}")
    formato = detectar_formato(ruta)
    if formato == "desconocido":
        raise ValueError(f"{ruta.name} no es una planilla Excel (.xlsx, .xlsb o .xls)")
    informar(f"Formato real de {ruta.name}: {formato}")
    ruta_xlsx = ruta
    if formato in ("xlsb", "xls"):
        informar(f"Convirtiendo {formato} a xlsx con LibreOffice…")
        try:
            ruta_xlsx = convertir_con_libreoffice(ruta, carpeta_trabajo)
        except RuntimeError as exc:
            raise ValueError(
                f"El archivo es {formato} y no se pudo convertir ({exc}). Ábrelo en Excel y guárdalo como "
                "«Libro de Excel (.xlsx)»."
            ) from exc
    informar("Leyendo la planilla…")
    libro = leer_libro(ruta_xlsx, config, formato=formato, ruta_original=ruta)
    informar("Revisando las reglas…")
    antes, ctx = auditar(libro, config)
    cambios, avisos = consolidar_cambios(libro, antes)
    informar("Calculando el puntaje con las correcciones automáticas…")
    despues, _ = auditar(aplicar_en_memoria(libro, cambios), config, incluir_cache=False)
    return Analisis(
        ruta, Path(ruta_xlsx), nombre_base(ruta), formato, config, libro, ctx, antes, cambios, avisos,
        despues, calcular_puntaje(libro, antes, config), calcular_puntaje(libro, despues, config),
    )


# --------------------------------------------------------------------------- decisiones


def ubicar(libro: Libro, hoja: str, celda: str) -> tuple[Tabla, int, str]:
    """(tabla, fila, clave de columna) de una celda que el auditor conoce."""
    m = re.fullmatch(r"([A-Z]{1,3})(\d+)", celda or "")
    if not m:
        raise ErrorDecision(f"Celda inválida: {celda!r}")
    try:
        tabla = libro.tabla(hoja)
    except KeyError:
        raise ErrorDecision(f"No se puede editar la hoja {hoja!r} desde aquí") from None
    clave = tabla.clave_de_letra(m.group(1))
    fila = int(m.group(2))
    if clave is None or fila not in tabla.valores.index:
        raise ErrorDecision(f"{hoja}!{celda} no es una celda de datos que se pueda editar desde aquí")
    return tabla, fila, clave


def es_editable(libro: Libro, hoja: str | None, celda: str | None) -> bool:
    if not hoja or not celda:
        return False
    try:
        tabla, fila, clave = ubicar(libro, hoja, celda)
    except ErrorDecision:
        return False
    return not tabla.es_formula.at[fila, clave] or clave in SOBRESCRIBIBLES


def tipo_de_columna(clave: str) -> str:
    if clave in NUMERICAS:
        return "numero"
    return {"fecha": "fecha", "hora": "hora"}.get(clave, "texto")


def convertir_valor(clave: str, texto) -> object:
    """Convierte lo que escribió el usuario al valor que se guarda en la celda."""
    if texto is None or (isinstance(texto, str) and limpiar(texto) == ""):
        return None
    if not isinstance(texto, str):
        return texto
    tipo = tipo_de_columna(clave)
    if clave == "abundancia" and es_presencia(texto):
        return PRESENCIA
    if tipo == "numero":
        n = a_numero(texto)
        if n is None:
            raise ErrorDecision(f"«{texto}» no es un número" + (" (o «P» para presencia)" if clave == "abundancia" else ""))
        return numero_para_celda(n)
    if tipo == "fecha":
        d = a_fecha(texto)
        if d is None:
            raise ErrorDecision(f"«{texto}» no es una fecha (usa dd-mm-aaaa)")
        return fecha_a_excel(d)
    if tipo == "hora":
        h = a_hora(texto)
        if h is None:
            raise ErrorDecision(f"«{texto}» no es una hora (usa hh:mm)")
        return hora_a_excel(h)
    return limpiar(texto)


def preparar_decision(analisis: Analisis, h: Hallazgo, datos: dict) -> Decision:
    """Valida y convierte lo que llega de la interfaz.

    datos = {"tipo": "valor", "texto": "…"} · {"tipo": "accion", "indice": i}
            · {"tipo": "ok"} · {"tipo": "sin_auto"}; todas admiten "nota".
    """
    tipo = datos.get("tipo")
    nota = limpiar(datos.get("nota") or "")
    if tipo == "ok":
        return Decision("ok", nota=nota)
    if tipo == "sin_auto":
        if not h.auto_corregible:
            raise ErrorDecision("Este hallazgo no tiene corrección automática")
        return Decision("sin_auto", nota=nota)
    if tipo == "accion":
        try:
            a = h.acciones[int(datos.get("indice"))]
        except (IndexError, TypeError, ValueError):
            raise ErrorDecision("Acción inexistente") from None
        hoja, celda, valor, texto = a.hoja or h.hoja, a.celda or h.celda, a.valor, a.etiqueta
    elif tipo == "valor":
        hoja, celda = datos.get("hoja") or h.hoja, datos.get("celda") or h.celda
        texto = datos.get("texto") or ""
        valor = None
    else:
        raise ErrorDecision(f"Tipo de decisión desconocido: {tipo!r}")
    if not hoja or not celda:
        raise ErrorDecision("Este hallazgo no apunta a una celda: corrígelo en Excel")
    tabla, fila, clave = ubicar(analisis.libro, hoja, celda)
    es_formula = bool(tabla.es_formula.at[fila, clave])
    if es_formula and clave not in SOBRESCRIBIBLES:
        raise ErrorDecision(
            f"{hoja}!{celda} tiene una fórmula (columna naranja): corrige el dato del que depende"
        )
    if tipo == "valor":
        valor = convertir_valor(clave, texto)
    return Decision("valor", valor, hoja, celda, str(texto), nota, reemplaza_formula=es_formula)


def cambios_con_decisiones(
    analisis: Analisis, decisiones: dict[str, Decision]
) -> tuple[list[Cambio], set[str]]:
    """Cambios a escribir (auto-correcciones aceptadas + valores del usuario) y las claves de los
    hallazgos que quedan resueltos por ellos."""
    rechazadas = {
        (h.correccion.hoja, h.correccion.celda)
        for k, d in decisiones.items()
        if d.tipo == "sin_auto" and (h := analisis.por_clave.get(k)) and h.correccion
    }
    manuales: dict[tuple[str, str], Cambio] = {}
    resueltos: set[str] = set()
    for k, d in decisiones.items():
        if d.tipo != "valor" or k not in analisis.por_clave:
            continue
        tabla, fila, clave = ubicar(analisis.libro, d.hoja, d.celda)
        regla = analisis.por_clave[k].id_regla
        if (d.hoja, d.celda) in manuales:
            manuales[(d.hoja, d.celda)].reglas.append(f"MANUAL:{regla}")
        else:
            manuales[(d.hoja, d.celda)] = Cambio(
                d.hoja, d.celda, fila, clave, tabla.valor(fila, clave), d.valor, [f"MANUAL:{regla}"],
                reemplaza_formula=d.reemplaza_formula,
            )
        resueltos.add(k)
    cambios = [
        c for c in analisis.cambios_auto
        if (c.hoja, c.celda) not in rechazadas and (c.hoja, c.celda) not in manuales
    ]
    celdas_auto = {(c.hoja, c.celda) for c in cambios}
    resueltos |= {
        h.clave() for h in analisis.antes
        if h.correccion and (h.correccion.hoja, h.correccion.celda) in celdas_auto
    }
    return cambios + list(manuales.values()), resueltos


def aceptados(decisiones: dict[str, Decision]) -> set[str]:
    return {k for k, d in decisiones.items() if d.tipo == "ok"}


def puntaje_con_aceptados(libro: Libro, hallazgos: list[Hallazgo], decisiones, config) -> Puntaje:
    """Los hallazgos que el usuario marcó «está bien así» no restan puntaje."""
    ok = aceptados(decisiones)
    return calcular_puntaje(libro, [h for h in hallazgos if h.clave() not in ok], config)


def proyectar(analisis: Analisis, decisiones: dict[str, Decision]) -> tuple[list[Hallazgo], Puntaje]:
    """Resultado esperado si se generara ahora (en memoria, sin escribir archivos)."""
    cambios, _ = cambios_con_decisiones(analisis, decisiones)
    libro = aplicar_en_memoria(analisis.libro, cambios)
    hallazgos, _ = auditar(libro, analisis.config, incluir_cache=False)
    return hallazgos, puntaje_con_aceptados(libro, hallazgos, decisiones, analisis.config)


# --------------------------------------------------------------------------- salidas


GRUPOS_ORIGEN = ("domesticas", "exoticas")


def exclusiones(config: dict, elegidas: dict | None = None) -> dict[str, bool]:
    """Si las domésticas y las exóticas quedan fuera del análisis: lo del config, con lo que se eligió encima."""
    out = {g: bool(config[g].get("excluir_del_analisis", g == "domesticas")) for g in GRUPOS_ORIGEN}
    out.update({g: bool(v) for g, v in (elegidas or {}).items() if g in GRUPOS_ORIGEN})
    return out


def config_con_exclusiones(config: dict, elegidas: dict | None) -> dict:
    if not elegidas:
        return config
    cfg = dict(config)
    for g, v in exclusiones(config, elegidas).items():
        cfg[g] = {**config[g], "excluir_del_analisis": v}
    return cfg


def generar(
    analisis: Analisis, carpeta: Path, decisiones: dict[str, Decision] | None = None,
    solo_reporte: bool = False, con_libreoffice: bool = True, informar=print, excluir: dict | None = None,
) -> Resultado:
    """excluir: {"domesticas": bool, "exoticas": bool} para base_limpia_larga.csv (por defecto, el config)."""
    decisiones = decisiones or {}
    cfg, libro, nombre = config_con_exclusiones(analisis.config, excluir), analisis.libro, analisis.nombre
    carpeta = Path(carpeta)
    cambios, resueltos = cambios_con_decisiones(analisis, decisiones)
    libro_despues = aplicar_en_memoria(libro, cambios)
    despues, _ = auditar(libro_despues, cfg, incluir_cache=False)
    archivos: dict[str, Path] = {}
    verificacion = None
    libro_base, ctx_base, hallazgos_base = libro, analisis.ctx, analisis.antes
    if solo_reporte:
        resueltos = set()
    else:
        informar("Escribiendo la planilla corregida…")
        corregida = carpeta / f"{nombre}_CORREGIDA.xlsx"
        por_hoja = cambios_por_hoja(cambios)
        permitir = {}
        for c in cambios:
            if c.reemplaza_formula:
                permitir.setdefault(c.hoja, set()).add(c.celda)
        escribir_con_cambios(analisis.ruta_xlsx, corregida, por_hoja, permitir)
        archivos["corregida"] = corregida
        archivos["cambios"] = carpeta / "cambios_aplicados.csv"
        escribir_cambios_csv(archivos["cambios"], cambios, cfg)
        informar("Verificando la planilla corregida…")
        verificacion = verificar_corregida(
            analisis.ruta_xlsx, corregida, por_hoja, con_libreoffice, cfg,
            formulas_reemplazadas=sum(c.reemplaza_formula for c in cambios),
        )
        # El "después" oficial es el que da volver a leer y auditar el archivo escrito.
        libro_c = leer_libro(corregida, cfg, formato="xlsx", ruta_original=analisis.ruta)
        reauditoria, ctx_c = auditar(libro_c, cfg, incluir_cache=False)
        a, b = {h.firma() for h in despues}, {h.firma() for h in reauditoria}
        verificacion.reauditoria_coincide = a == b
        verificacion.diferencias_reauditoria = [" / ".join(map(str, x)) for x in sorted(a ^ b)][:20]
        despues = reauditoria
        libro_base, ctx_base, hallazgos_base = libro_c, ctx_c, reauditoria

    p_despues = puntaje_con_aceptados(libro_despues, despues, decisiones, cfg)
    pendientes = {h.firma() for h in despues}
    textos = {k: d.describir() for k, d in decisiones.items()}
    estadisticas = analisis.ctx.estadisticas

    informar("Escribiendo el archivo de revisión…")
    archivos["revision"] = carpeta / f"{nombre}_REVISION.xlsx"
    escribir_revision(
        analisis.ruta_xlsx, archivos["revision"], analisis.ruta.name, analisis.formato, analisis.antes,
        despues, analisis.puntaje_antes, p_despues, resueltos, cfg, estadisticas, len(cambios), textos,
    )
    archivos["hallazgos"] = carpeta / "hallazgos.csv"
    escribir_hallazgos_csv(archivos["hallazgos"], analisis.antes, resueltos, pendientes, textos, cfg)
    archivos["base"] = carpeta / "base_limpia_larga.csv"
    escribir_base_larga(archivos["base"], filas_base_larga(libro_base, ctx_base, hallazgos_base, cfg), cfg)
    archivos["reporte"] = carpeta / "reporte.md"
    orden = ["revision", "corregida", "cambios", "reporte", "base", "hallazgos"]
    escribir_reporte(
        archivos["reporte"], nombre=nombre, libro=libro, antes=analisis.puntaje_antes, despues=p_despues,
        hallazgos_antes=analisis.antes, hallazgos_despues=despues, cambios=cambios,
        avisos_cambios=analisis.avisos_cambios, verificacion=verificacion, estadisticas=estadisticas,
        config=cfg, solo_reporte=solo_reporte, archivos=[archivos[k].name for k in orden if k in archivos],
        decisiones={k: (analisis.por_clave[k], d) for k, d in decisiones.items() if k in analisis.por_clave},
        origen=resumen_origen(ctx_base, cfg),
    )
    return Resultado(
        carpeta, nombre, libro, analisis.antes, despues, analisis.puntaje_antes, p_despues, cambios,
        analisis.avisos_cambios, verificacion, estadisticas, archivos, dict(decisiones),
        len(aceptados(decisiones)),
    )
