"""Acceso directo al XML de un .xlsx.

openpyxl descarta las extensiones x14 (listas desplegables, entre otras) al guardar.
Por eso el archivo corregido se escribe editando solo las celdas necesarias dentro de
``xl/worksheets/sheetN.xml``; todo lo demás del paquete se copia byte a byte.
"""

from __future__ import annotations

import html
import posixpath
import re
import zipfile
from collections import defaultdict
from pathlib import Path

from lxml import etree
from openpyxl.formula.translate import Translator
from openpyxl.utils import column_index_from_string, range_boundaries

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"

_REF_RE = re.compile(r"^([A-Z]{1,3})(\d+)$")
_ROW_RE = re.compile(r'<row\b[^>]*?\br="(\d+)"[^>]*?(/?)>')
_CELL_RE = re.compile(r"<c\b([^>]*?)(/>|>(.*?)</c>)", re.S)
_ATTR_RE = re.compile(r'([\w:]+)="([^"]*)"')
_XML_INVALIDO = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


class ErrorEdicionXML(Exception):
    pass


def dividir_ref(ref: str) -> tuple[str, int]:
    m = _REF_RE.match(ref)
    if not m:
        raise ErrorEdicionXML(f"Referencia de celda inválida: {ref!r}")
    return m.group(1), int(m.group(2))


def mapa_hojas(z: zipfile.ZipFile) -> dict[str, str]:
    """Nombre de hoja -> ruta de la parte XML (vía xl/workbook.xml + rels)."""
    wb = etree.fromstring(z.read("xl/workbook.xml"))
    rels = etree.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    destino = {}
    for rel in rels.findall(f"{{{NS_PKG_REL}}}Relationship"):
        target = rel.get("Target")
        if target.startswith("/"):
            ruta = target.lstrip("/")
        else:
            ruta = posixpath.normpath(posixpath.join("xl", target))
        destino[rel.get("Id")] = ruta
    hojas = {}
    for s in wb.iter(f"{{{NS_MAIN}}}sheet"):
        hojas[s.get("name")] = destino[s.get(f"{{{NS_REL}}}id")]
    return hojas


def contar_x14_validaciones(ruta: str | Path) -> dict[str, int]:
    """Cantidad de <x14:dataValidation> por hoja."""
    with zipfile.ZipFile(ruta) as z:
        return {
            hoja: z.read(parte).decode("utf-8").count("<x14:dataValidation ")
            for hoja, parte in mapa_hojas(z).items()
        }


def contar_formulas(ruta: str | Path) -> dict[str, int]:
    with zipfile.ZipFile(ruta) as z:
        return {
            hoja: len(re.findall(r"<f[ >/]", z.read(parte).decode("utf-8")))
            for hoja, parte in mapa_hojas(z).items()
        }


def validaciones_lista(xml: str) -> list[tuple[str, str]]:
    """(sqref, formula1) de las validaciones tipo lista, tanto x14 como estándar."""
    out = []
    for m in re.finditer(r"<x14:dataValidation\b(.*?)</x14:dataValidation>", xml, re.S):
        cuerpo = m.group(1)
        if 'type="list"' not in cuerpo.split(">", 1)[0]:
            continue
        f = re.search(r"<xm:f>(.*?)</xm:f>", cuerpo, re.S)
        sq = re.search(r"<xm:sqref>(.*?)</xm:sqref>", cuerpo, re.S)
        if f and sq:
            out.append((sq.group(1).strip(), html.unescape(f.group(1).strip())))
    for m in re.finditer(r"<dataValidation\b([^>]*)>(.*?)</dataValidation>", xml, re.S):
        attrs, cuerpo = m.group(1), m.group(2)
        if 'type="list"' not in attrs:
            continue
        sq = re.search(r'sqref="([^"]+)"', attrs)
        f = re.search(r"<formula1>(.*?)</formula1>", cuerpo, re.S)
        if f and sq:
            out.append((sq.group(1).strip(), html.unescape(f.group(1).strip())))
    return out


def columnas_de_sqref(sqref: str) -> set[str]:
    from openpyxl.utils import get_column_letter

    cols = set()
    for rango in sqref.split():
        c1, _, c2, _ = range_boundaries(rango)
        cols.update(get_column_letter(c) for c in range(c1, c2 + 1))
    return cols


# --------------------------------------------------------------------------- edición


def _escapar(s: str) -> str:
    s = _XML_INVALIDO.sub("", s)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _formatear_numero(v) -> str:
    if isinstance(v, bool):
        raise ErrorEdicionXML("Los booleanos no se escriben como número")
    if isinstance(v, int) or (isinstance(v, float) and v.is_integer()):
        return str(int(v))
    return repr(float(v))


def construir_celda(ref: str, atributos_previos: list[tuple[str, str]], valor) -> str:
    """Celda nueva conservando el estilo (s) y demás atributos que no dependen del valor."""
    keep = [(k, v) for k, v in atributos_previos if k not in ("r", "t", "cm", "vm")]
    attrs = f'r="{ref}"' + "".join(f' {k}="{v}"' for k, v in keep)
    if valor is None:
        return f"<c {attrs}/>"
    if isinstance(valor, str):
        return f'<c {attrs} t="inlineStr"><is><t xml:space="preserve">{_escapar(valor)}</t></is></c>'
    return f"<c {attrs}><v>{_formatear_numero(valor)}</v></c>"


def _estilos_de_columna(xml: str) -> dict[int, str]:
    estilos = {}
    m = re.search(r"<cols>(.*?)</cols>", xml, re.S)
    if not m:
        return estilos
    for c in re.finditer(r"<col\b([^>]*)/?>", m.group(1)):
        a = dict(_ATTR_RE.findall(c.group(1)))
        if "style" in a:
            for i in range(int(a["min"]), int(a["max"]) + 1):
                estilos[i] = a["style"]
    return estilos


def _editar_fila(fila_xml: str, num_fila: int, cambios: dict[str, object], estilos_col, permitir=()) -> str:
    if fila_xml.endswith("/>"):
        apertura, cuerpo = fila_xml[:-2] + ">", ""
    else:
        fin_apertura = fila_xml.index(">") + 1
        apertura, cuerpo = fila_xml[:fin_apertura], fila_xml[fin_apertura : -len("</row>")]

    celdas = []  # (inicio, fin, ref, atributos, contenido)
    for m in _CELL_RE.finditer(cuerpo):
        attrs = _ATTR_RE.findall(m.group(1))
        ref = dict(attrs).get("r")
        celdas.append([m.start(), m.end(), ref, attrs, m.group(3) or ""])

    reemplazos = []  # (inicio, fin, texto)
    for ref, valor in cambios.items():
        col, fila = dividir_ref(ref)
        if fila != num_fila:
            raise ErrorEdicionXML(f"{ref} no pertenece a la fila {num_fila}")
        existente = next((c for c in celdas if c[2] == ref), None)
        if existente is not None:
            if "<f" in existente[4] and ref not in permitir:
                raise ErrorEdicionXML(f"{ref} contiene una fórmula; no se sobrescribe")
            reemplazos.append((existente[0], existente[1], construir_celda(ref, existente[3], valor)))
            continue
        idx = column_index_from_string(col)
        attrs = [("s", estilos_col[idx])] if idx in estilos_col else []
        pos = len(cuerpo)
        for c in celdas:
            if c[2] and column_index_from_string(dividir_ref(c[2])[0]) > idx:
                pos = c[0]
                break
        reemplazos.append((pos, pos, construir_celda(ref, attrs, valor)))

    for ini, fin, texto in sorted(reemplazos, key=lambda r: (r[0], r[1]), reverse=True):
        cuerpo = cuerpo[:ini] + texto + cuerpo[fin:]
    return apertura + cuerpo + "</row>"


def _descompartir(xml: str, ref_maestra: str) -> str:
    """Si la celda es la maestra de una fórmula compartida, escribe la fórmula explícita en cada
    celda del grupo para poder sobrescribir la maestra sin romper las demás."""
    m = re.search(rf'<c r="{ref_maestra}"[^>]*>(.*?)</c>', xml, re.S)
    f = re.search(r"<f\b([^>]*)>(.*?)</f>", m.group(1), re.S) if m else None
    if not f:
        return xml
    attrs = dict(_ATTR_RE.findall(f.group(1)))
    if attrs.get("t") != "shared" or "ref" not in attrs:
        return xml
    si, texto = attrs["si"], html.unescape(f.group(2))
    hija = re.compile(rf'<f\b(?=[^>]*\bt="shared")(?=[^>]*\bsi="{si}")[^>]*/>')
    ediciones = []
    for c in _CELL_RE.finditer(xml):
        contenido = c.group(3) or ""
        ref = dict(_ATTR_RE.findall(c.group(1))).get("r")
        if ref == ref_maestra:
            fm = re.search(r"<f\b[^>]*>.*?</f>", contenido, re.S)
            nuevo = contenido[: fm.start()] + f"<f>{_escapar(texto)}</f>" + contenido[fm.end():]
        elif hija.search(contenido):
            traducida = Translator("=" + texto, origin=ref_maestra).translate_formula(ref)[1:]
            nuevo = hija.sub(f"<f>{_escapar(traducida)}</f>", contenido)
        else:
            continue
        ini = c.start(3)
        ediciones.append((ini, ini + len(contenido), nuevo))
    for ini, fin, texto_nuevo in reversed(ediciones):
        xml = xml[:ini] + texto_nuevo + xml[fin:]
    return xml


def aplicar_cambios_hoja(xml: str, cambios: dict[str, object], permitir_formula=()) -> str:
    """Aplica {ref: valor} sobre el XML de una hoja. Valor: número, texto o None (vaciar).

    Las celdas con fórmula se rechazan, salvo las de ``permitir_formula``.
    """
    if not cambios:
        return xml
    for ref in permitir_formula:
        if ref in cambios:
            xml = _descompartir(xml, ref)
    ini_sd = xml.find("<sheetData")
    if ini_sd < 0:
        raise ErrorEdicionXML("La hoja no tiene <sheetData>")
    if xml.startswith("<sheetData/>", ini_sd):
        xml = xml[:ini_sd] + "<sheetData></sheetData>" + xml[ini_sd + len("<sheetData/>") :]
    ini_cuerpo = xml.index(">", ini_sd) + 1
    fin_sd = xml.index("</sheetData>", ini_cuerpo)
    estilos_col = _estilos_de_columna(xml)

    por_fila: dict[int, dict] = defaultdict(dict)
    for ref, valor in cambios.items():
        por_fila[dividir_ref(ref)[1]][ref] = valor

    filas = {}
    for m in _ROW_RE.finditer(xml, ini_cuerpo, fin_sd):
        n = int(m.group(1))
        fin = m.end() if m.group(2) == "/" else xml.index("</row>", m.end()) + len("</row>")
        filas[n] = (m.start(), fin)

    ediciones = []  # (inicio, fin, texto)
    for n, cambios_fila in por_fila.items():
        if n in filas:
            ini, fin = filas[n]
            ediciones.append((ini, fin, _editar_fila(xml[ini:fin], n, cambios_fila, estilos_col, permitir_formula)))
        else:
            siguiente = min((v[0] for k, v in filas.items() if k > n), default=fin_sd)
            nueva = _editar_fila(f'<row r="{n}"/>', n, cambios_fila, estilos_col)
            ediciones.append((siguiente, siguiente, nueva))

    for ini, fin, texto in sorted(ediciones, key=lambda e: (e[0], e[1]), reverse=True):
        xml = xml[:ini] + texto + xml[fin:]
    return xml


def activar_recalculo(wb_xml: str) -> str:
    """Pone fullCalcOnLoad="1" en <calcPr> (lo crea si no existe)."""
    m = re.search(r"<calcPr\b([^>]*?)(/?)>", wb_xml)
    if m:
        attrs = m.group(1)
        if "fullCalcOnLoad=" in attrs:
            attrs = re.sub(r'fullCalcOnLoad="[^"]*"', 'fullCalcOnLoad="1"', attrs)
        else:
            attrs += ' fullCalcOnLoad="1"'
        return wb_xml[: m.start()] + f"<calcPr{attrs}{m.group(2)}>" + wb_xml[m.end() :]
    for etiqueta in (
        "<oleSize", "<customWorkbookViews", "<pivotCaches", "<smartTagPr", "<smartTagTypes",
        "<webPublishing", "<fileRecoveryPr", "<webPublishObjects", "<extLst", "</workbook>",
    ):
        i = wb_xml.find(etiqueta)
        if i >= 0:
            return wb_xml[:i] + '<calcPr fullCalcOnLoad="1"/>' + wb_xml[i:]
    raise ErrorEdicionXML("workbook.xml sin cierre </workbook>")


def _quitar_calcchain(nuevos: dict[str, bytes], zi: zipfile.ZipFile) -> set[str]:
    """Quita xl/calcChain.xml (y sus referencias): Excel lo rehace al abrir. Es necesario si se
    reemplazó alguna fórmula por un valor, porque la cadena apuntaría a celdas sin fórmula."""
    rels = "xl/_rels/workbook.xml.rels"
    nuevos[rels] = re.sub(
        r'<Relationship\b[^>]*Target="[^"]*calcChain\.xml"[^>]*/>', "", zi.read(rels).decode("utf-8")
    ).encode("utf-8")
    tipos = "[Content_Types].xml"
    nuevos[tipos] = re.sub(
        r'<Override\b[^>]*PartName="/xl/calcChain\.xml"[^>]*/>', "", zi.read(tipos).decode("utf-8")
    ).encode("utf-8")
    return {"xl/calcChain.xml"}


def escribir_con_cambios(
    original: str | Path, destino: str | Path, cambios: dict[str, dict[str, object]],
    permitir_formula: dict[str, set[str]] | None = None,
) -> None:
    """Copia el paquete .xlsx aplicando {hoja: {ref: valor}}. Nunca toca el original.

    ``permitir_formula`` = {hoja: {ref}} de celdas con fórmula que sí se pueden sobrescribir.
    """
    permitir_formula = permitir_formula or {}
    original, destino = Path(original).resolve(), Path(destino).resolve()
    if original == destino:
        raise ErrorEdicionXML("El destino no puede ser el archivo original")
    temporal = destino.with_name(destino.name + ".tmp")
    with zipfile.ZipFile(original) as zi:
        partes = mapa_hojas(zi)
        desconocidas = set(cambios) - set(partes)
        if desconocidas:
            raise ErrorEdicionXML(f"Hojas inexistentes: {sorted(desconocidas)}")
        nuevos: dict[str, bytes] = {}
        omitir: set[str] = set()
        formulas_quitadas = False
        for hoja, cambios_hoja in cambios.items():
            if cambios_hoja:
                xml = zi.read(partes[hoja]).decode("utf-8")
                xml_nuevo = aplicar_cambios_hoja(xml, cambios_hoja, permitir_formula.get(hoja, ()))
                etree.fromstring(xml_nuevo.encode("utf-8"))  # debe seguir siendo XML válido
                nuevos[partes[hoja]] = xml_nuevo.encode("utf-8")
                formulas_quitadas |= len(re.findall(r"<f[ >/]", xml_nuevo)) < len(re.findall(r"<f[ >/]", xml))
        if formulas_quitadas and "xl/calcChain.xml" in zi.namelist():
            omitir = _quitar_calcchain(nuevos, zi)
        nuevos["xl/workbook.xml"] = activar_recalculo(
            zi.read("xl/workbook.xml").decode("utf-8")
        ).encode("utf-8")
        with zipfile.ZipFile(temporal, "w") as zo:
            for info in zi.infolist():
                if info.filename in omitir:
                    continue
                datos = nuevos.get(info.filename)
                if datos is None:
                    datos = zi.read(info.filename)
                zo.writestr(info, datos, compress_type=info.compress_type)
    temporal.replace(destino)
