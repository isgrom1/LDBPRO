# Auditor de planillas de fauna XPE (LdB SEIA)

Revisa una planilla de terreno con el formato XPE (`Formato_Planilla_Fauna_XPE_Externo_v4`),
detecta los errores que arruinan los análisis (diversidad, densidades, curvas de acumulación
en R/PAST), aplica solo las correcciones seguras y deja el resto para que lo decidas tú.

## Aplicación de escritorio (`AuditorXPE.exe`)

Doble clic en **AuditorXPE.exe** y se abre la ventana del programa (todo corre en tu PC, sin
internet). La ventana usa WebView2, el motor que ya trae Windows 10/11; si faltara, el programa
se abre en el navegador como respaldo. Solo se abre una copia: si haces doble clic otra vez, se
trae al frente la que ya está abierta.

1. **Subir**: arrastra la planilla (o prueba con la planilla de ejemplo).
2. **Revisar**: puntaje y estado, y los problemas agrupados por estación o por tipo, con
   filtros por severidad y buscador. En cada problema puedes:
   - dejar o quitar la **corrección automática**;
   - usar un **valor sugerido** con un clic («Poner 1», «Usar «Matorral medio-alto»»…);
   - **escribir otro valor** (o elegirlo de la lista de `validación datos`; los nombres de
     especie se autocompletan desde `nombres BD`);
   - marcarlo **«Está bien así»**, con una nota (no resta puntaje).
   El puntaje se recalcula con cada decisión.
3. **Descargar**: genera la planilla corregida con tus decisiones, muestra el puntaje final y
   la verificación del archivo. Cada archivo tiene un botón **Abrir** (Excel, el bloc de
   notas…) y **📂 Abrir carpeta** muestra todos los resultados en el Explorador; también se
   puede guardar una copia o todo en un .zip.

Las revisiones quedan en `Documentos\Auditor XPE\` (una carpeta por planilla, con
`sesion.json`). Si vuelves a abrir el mismo archivo, se recuperan tus decisiones. Un
`config.yaml` junto al .exe (o en esa carpeta) cambia los umbrales; si algo falla, el detalle
queda en `Documentos\Auditor XPE\registro.log`.

**Descargar el .exe**: en GitHub, pestaña *Actions* → *Ejecutable para Windows* → la última
ejecución en verde → artefacto **AuditorXPE-windows** (un .zip con el .exe). Como el .exe no
está firmado, la primera vez Windows puede mostrar «Windows protegió su PC»: *Más información →
Ejecutar de todas formas*.

**Compilarlo en Windows** (opcional): `pip install -r requirements.txt pyinstaller==6.22.3` y
`pyinstaller --noconfirm --clean empaquetado/AuditorXPE.spec` → `dist\AuditorXPE.exe`.
Sin compilar: `python AuditorXPE.py`.

## Línea de comandos

```
python auditar_planilla.py ruta/planilla.xlsx [--config config.yaml] [--solo-reporte]
```

Opciones extra: `--salida CARPETA` (por defecto, junto a la planilla) y `--sin-libreoffice`
(omite la verificación del recálculo).

## Instalación

```
pip install -r requirements.txt
```

LibreOffice (`soffice`) es opcional: se usa para verificar que las fórmulas del archivo
corregido recalculan sin errores nuevos y para convertir archivos `.xlsb`/`.xls`.

## Qué entrega

Una carpeta `auditoria_<nombre>_<fecha>/` (si ya existe, se crea `_2`, `_3`…; nunca se
sobrescribe una auditoría anterior, que puede tener decisiones anotadas):

| Archivo | Contenido |
|---|---|
| `<nombre>_REVISION.xlsx` | Copia para mirar: celdas coloreadas por severidad (rojo/naranja/amarillo) con comentario (problema + sugerencia). Hojas nuevas `RESUMEN` (puntaje, estado, conteos, top 10) y `HALLAZGOS` (tabla con filtros, vínculo a cada celda y columna vacía «Decisión / Corrección»). Escrita con openpyxl: **pierde las listas desplegables**, es solo para revisar. |
| `<nombre>_CORREGIDA.xlsx` | El original con **solo** las auto-correcciones seguras, editado a nivel XML: conserva listas desplegables (`x14:dataValidation`), fórmulas y estilos; `fullCalcOnLoad="1"` para que Excel recalcule al abrir. |
| `cambios_aplicados.csv` | hoja, celda, valor anterior, valor nuevo y regla de cada corrección. |
| `reporte.md` | Puntaje antes/después, qué se corrigió solo, qué falta decidir agrupado por estación, verificación del archivo corregido y mapeo de columnas detectado. |
| `base_limpia_larga.csv` | Tabla larga para R/PAST con las uniones rehechas en código. No borra filas: `tipo_abundancia` (conteo / presencia «P»), `origen` (doméstica / exótica / vacío si es nativa o no está en las listas), `excluir_motivo` (doméstica —se puede cambiar—, duplicado marcado en la revisión, estación inexistente, especie fuera de nombres BD), `usar_en_abundancia` (no para presencia, evidencia indirecta o abundancia no válida) y largo/ancho/área de transecto para densidades. |
| `hallazgos.csv` | Los mismos hallazgos de la hoja HALLAZGOS, en CSV. |

Los CSV salen en el formato de Excel en español, para abrirlos con doble clic: columnas
separadas por `;`, coma decimal (`71,6`) y UTF-8 con BOM (tildes y emojis bien). En R se leen
con `read.csv2("base_limpia_larga.csv", fileEncoding = "UTF-8-BOM")` o `readr::read_csv2()`.
Con Excel en inglés, cambia `csv.separador` a `","` y `csv.decimal` a `"."` en `config.yaml`.

Con `--solo-reporte` no se escriben `_CORREGIDA.xlsx` ni `cambios_aplicados.csv`; el reporte
muestra el puntaje *proyectado* tras las auto-correcciones.

## Cómo trabaja

- **Verifica la estructura al abrir**: ubica cada columna por el texto del encabezado (no por
  la letra), confirma la fila de encabezados (Estaciones fila 2, Registros fila 9) e imprime el
  mapeo detectado.
- **No confía en los valores cacheados de las fórmulas**: rehace en pandas las uniones
  Registros↔Estaciones (por `ID Estación`) y Registros↔nombres BD (por `Nombre común`). La
  unión es exacta, como en R; la que ignora mayúsculas/espacios solo sirve para diagnosticar.
  La regla X02 avisa cuando el valor guardado de una fórmula no coincide con lo que da al
  recalcular (archivo editado fuera de Excel).
- **Valores permitidos desde la planilla**: metodologías, tipos de registro y laderas se leen de
  las propias listas desplegables (rangos de `validación datos`); comportamiento, del
  encabezado de `validación datos`; los tipos Directo/Indirecto, de la fórmula de la columna AB.
- **Formato real**: detecta `.xlsb`/`.xls` aunque la extensión diga `.xlsx` y los convierte con
  LibreOffice antes de auditar.
- **Registros TA** se audita solo si tiene datos. Una fila es registro solo si tiene `ID Estación`.

## Reglas

| Regla | Qué revisa | Sev. | Auto-fix |
|---|---|---|---|
| E01 | ID de estación duplicado | 🔴 | No |
| E02 | Ambiente vacío | 🔴 | No |
| E03 | Variantes del mismo ambiente | 🟠 | No |
| E04 | ID Final Estación vacío o distinto del ID | 🟠 | Sí¹ |
| E05 | Coordenada no numérica | 🔴 | Sí |
| E06 | Coordenada vacía o 0 | 🔴 | No |
| E07 | Coordenada fuera de lugar (posible error de dígito) | 🟠 | No |
| E08 | Largo del transecto fuera de 150–250 m: 🟡 si es corto o algo largo (terreno; el área usa el largo real), 🟠 bajo 10 m o sobre 400 m (probable error de coordenada) | 🟡/🟠 | No |
| E09 | Coordenadas idénticas a las de otra estación (copy-paste) | 🔴 | No |
| E10 | Punto de la estación a más de 150 m del punto medio del transecto: 🟡 hasta 500 m (punto inaccesible), 🟠 más lejos | 🟡/🟠 | No |
| E11 | Metodologías con valores distintos de 1/0 | 🟠 | Sí |
| E12 | Estación sin registros | 🟡 | No |
| E13 | Proyecto/Región/Temporada inconsistentes entre filas | 🟠 | Sí² |
| E14 | Ladera vacía o fuera de la lista de validación | 🟡 | No |
| E15 | Fila con datos pero sin ID Estación | 🟠 | No |
| R01 | ID Estación no existe en Estaciones | 🔴 | No |
| R02 | Nombre común no existe en nombres BD | 🔴 | No |
| R03 | Nombre común existe en nombres BD solo ignorando mayúsculas/espacios | 🔴 | Sí |
| R04 | Nombre genérico (sp./spp.) | 🟠 | No |
| R05 | Mismo nombre científico escrito con distintos nombres comunes | 🟠 | Sí |
| R06 | Abundancia vacía, no numérica, ≤0 o decimal (la «P» de presencia no es error: R26) | 🔴 | No |
| R07 | Abundancia alta para la especie (desde 10 individuos; no revisa aves de bandada: paseriformes, tórtolas, patos, playeros, jotes…) | 🟡 | No |
| R08 | Especie doméstica | 🟡 | No |
| R09 | Fecha vacía o fuera del rango de la campaña | 🟠 | No |
| R10 | Hora vacía | 🟠 | No |
| R11 | % de registros de aves en transecto entre 12 y 17 h: solo un indicador (pantalla, informe y RESUMEN), no aparece como problema | — | No |
| R12 | Playback (aves o anfibios) hecho claramente de día, o cámara trampa a las 00:00 exactas | 🟠 | No |
| R13 | Metodología o tipo de registro fuera de la lista de validación | 🟠 | No |
| R14 | Directo/Indirecto incoherente con el tipo de registro | 🟠 | No |
| R15 | Registro duplicado: 🟠 si las filas son idénticas, 🟡 si cambia algo (fotos, comportamiento…), con las dos filas lado a lado; macho/hembra o adulto/juvenil no cuentan como duplicado | 🟡/🟠 | No |
| R16 | Coordenada de captura lejos de su estación | 🟠 | No |
| R17 | Texto con duda en Observaciones o Fotos | 🟡 | No |
| R18 | Fotos con separadores o códigos inconsistentes | 🟡 | No |
| R19 | Clase incompatible con la metodología | 🟠 | No |
| R20 | Metodología del registro no activada en Estaciones | 🟡 | No |
| R21 | Comportamiento fuera de la lista de validación | 🟡 | No |
| R22 | Número guardado como texto (captura o abundancia) | 🟠 | Sí |
| R23 | Observación menciona otra metodología | 🟡 | No |
| R24 | Fila con datos pero sin ID Estación | 🟠 | No |
| R25 | Especie exótica (conejo, liebre, gorrión, codorniz, guarén…): se informa, no se excluye | 🟡 | No |
| R26 | Registro de presencia «P» (sin conteo): cuenta para riqueza y presencia/ausencia, no para abundancia | 🟡 | Sí⁴ |
| R27 | Nombre común que en nombres BD tiene varias especies (la planilla completa la primera) | 🟡 | No |
| B01 | Lista de especies de la plantilla (nombres BD) con entradas problemáticas: solo aviso | 🟡 | No |
| T01 | Plantilla: una fórmula y su lista de validación no coinciden (p. ej. «Huella» vs «Huellas») | 🟡 | Sí³ |
| X01 | Columna de fórmula con un valor fijo | 🟠 | No |
| X02 | Valor cacheado de una fórmula desactualizado | 🟡 | No |

¹ Solo si está vacío o difiere en mayúsculas/espacios: un código distinto puede ser un renombre
deliberado para el informe. ² Solo si hay un valor mayoritario y la variante es un prefijo o un
typo claro (p. ej. «Punta del So» → «Punta del Sol»). ³ Corrige la celda de la lista en
`validación datos` para que coincida con la fórmula (no toca los registros).

**Domésticas y exóticas.** Se sigue el criterio de la LdB de XPE: el ganado, los perros y los
gatos se registran en terreno (van en la planilla que se entrega, no se borran), pero no entran
en la lista de especies ni en los análisis; las exóticas (conejo, liebre, gorrión, codorniz,
guarén…) sí entran, marcadas como «Exótico». En `base_limpia_larga.csv` quedan en la columna
`origen`, y las domésticas con `excluir_motivo = doméstica`. En la aplicación, una tarjeta en la
revisión las lista y permite dejar cada grupo dentro o fuera del análisis para esa planilla; en
la línea de comandos se usa `excluir_del_analisis` de `config.yaml`, donde también están las
listas.

⁴ Solo unifica variantes («p», «Presencia», «presente») a «P».

**Motivos de terreno.** Varios avisos traen un botón con el motivo habitual («No se pudo recorrer
completo», «No se pudo llegar al punto», «Son registros distintos», «Conteo real»): un clic lo
deja como «está bien así» con esa nota, que queda en el informe. En un duplicado, «Es el mismo» anota `[DUPLICADO de la fila N]` en
Observaciones (para borrar la fila antes de entregar) y lo saca de `base_limpia_larga.csv`.

**Abundancia «P».** La LdB de XPE usa «P: Presencia» en sus tablas de abundancia para especies
detectadas sin conteo (p. ej. una cururera que se escucha). El auditor la acepta: en la base
larga queda con `tipo_abundancia = presencia`, entra a la riqueza y a la presencia/ausencia
(curvas de acumulación, Chao2, diversidad beta) y no a abundancia, densidad ni Shannon/Simpson.

E14, E15, R21–R27, B01, T01, X01 y X02 son adicionales a la lista original.

### Agregar una regla

Cada regla es una función registrada que recibe el contexto (con los DataFrames y las uniones
ya hechas) y devuelve hallazgos. Créala en `auditor/reglas/` (en un módulo nuevo, impórtalo en
`auditor/reglas/__init__.py`):

```python
from ..hallazgos import Severidad, regla

@regla("R26", "Sexo fuera de la lista", Severidad.INFO)
def r26(ctx):
    for t, fila, u in ctx.iter_registros():       # u = unión rehecha (especie, clase, fecha, hora…)
        if t.valor(fila, "sexo") not in ("Macho", "Hembra", "Indeterminado"):
            yield ctx.hallazgo("R26", t, fila, "sexo", "Problema en español", "Sugerencia")
```

Para que sea auto-corregible, regístrala con `auto_corregible=True` y pasa
`correccion=<valor nuevo>` a `ctx.hallazgo`. El motor junta las correcciones (si dos reglas
proponen valores distintos para la misma celda no aplica ninguna), las aplica en memoria,
vuelve a auditar y escribe el archivo corregido.

## Puntaje de calidad

- % registros limpios = registros sin hallazgos 🔴 ni 🟠 en su fila / total de registros.
- % estaciones limpias, con la misma lógica.
- Índice (0–100) = 70 % × %registros + 30 % × %estaciones − 2 × (n.º de reglas 🔴 distintas).
- Estado: ✅ LISTA PARA ANÁLISIS (sin 🔴 ni 🟠) · ⚠️ USABLE CON REVISIÓN (sin 🔴, con 🟠) · ❌ NO USAR (hay 🔴).

El «después» se calcula volviendo a leer y auditar el archivo `_CORREGIDA.xlsx` ya escrito.

## Configuración

`config.yaml` trae los umbrales de largo de transecto, ancho de transecto por clase,
especies domésticas y exóticas, distancia máxima de captura, horario de actividad de aves
y el horario nocturno de los playback. El resto de los
parámetros (y sus valores por defecto) está en `auditor/config.py`; cualquiera se puede
sobrescribir desde el YAML. Los anchos de transecto son los de la LdB de XPE:
reptiles y anfibios 200 × 5 m, aves y mamíferos 200 × 50 m.

## Pruebas

```
python -m pytest
python tests/aceptacion.py        # imprime la tabla «esperado vs detectado»
```

La planilla de prueba **no se versiona** (el repositorio es público y la planilla tiene
coordenadas de especies, datos del proyecto y nombres de personas). Cópiala a
`tests/datos/Formato_Planilla_Fauna_XPE_Externo_v4_version_1.xlsb.xlsx` o indica su ruta con
la variable `PLANILLA_XPE`; sin ella, las pruebas que la necesitan se saltan.

Las pruebas de la aplicación y de las decisiones usan `auditor/demo.py`, una planilla de
ejemplo con datos ficticios (corren también en GitHub Actions, que además compila el .exe y
le hace una autoprueba: `AuditorXPE.exe --autoprueba` para el servidor y
`--autoprueba-ventana` para la ventana WebView2).

`tests/escenario.py` crea una copia de la planilla con los errores de la revisión manual que no
están en esa versión del archivo (PMF20, PMF29/PMF32, PMF35, Cururo «P», ID Final vacío,
«Punta del So»), para comprobar que las reglas los detectan.
