# Auditor de planillas de fauna XPE (LdB SEIA)

Revisa una planilla de terreno con el formato XPE (`Formato_Planilla_Fauna_XPE_Externo_v4`),
detecta los errores que arruinan los análisis (diversidad, densidades, curvas de acumulación
en R/PAST), aplica solo las correcciones seguras y deja el resto para que lo decidas tú.

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
| `base_limpia_larga.csv` | Tabla larga para R/PAST con las uniones rehechas en código. No borra filas: `excluir_motivo` (doméstica, estación inexistente, especie fuera de nombres BD), `usar_en_abundancia` (no para evidencia indirecta o abundancia no válida) y largo/ancho/área de transecto para densidades. |
| `hallazgos.csv` | Los mismos hallazgos de la hoja HALLAZGOS, en CSV. |

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
| E08 | Largo del transecto fuera de rango | 🟠 | No |
| E09 | Coordenadas idénticas a las de otra estación (copy-paste) | 🔴 | No |
| E10 | Punto de la estación lejos del punto medio del transecto | 🟠 | No |
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
| R06 | Abundancia vacía, no numérica, ≤0 o decimal | 🔴 | No |
| R07 | Abundancia atípica para la especie | 🟡 | No |
| R08 | Especie doméstica | 🟡 | No |
| R09 | Fecha vacía o fuera del rango de la campaña | 🟠 | No |
| R10 | Hora vacía | 🟠 | No |
| R11 | Aves en transecto fuera del horario de mayor actividad | 🟡 | No |
| R12 | Hora incoherente con la metodología | 🟠 | No |
| R13 | Metodología o tipo de registro fuera de la lista de validación | 🟠 | No |
| R14 | Directo/Indirecto incoherente con el tipo de registro | 🟠 | No |
| R15 | Registro duplicado | 🟠 | No |
| R16 | Coordenada de captura lejos de su estación | 🟠 | No |
| R17 | Texto con duda en Observaciones o Fotos | 🟡 | No |
| R18 | Fotos con separadores o códigos inconsistentes | 🟡 | No |
| R19 | Clase incompatible con la metodología | 🟠 | No |
| R20 | Metodología del registro no activada en Estaciones | 🟡 | No |
| R21 | Comportamiento fuera de la lista de validación | 🟡 | No |
| R22 | Número guardado como texto (captura o abundancia) | 🟠 | Sí |
| R23 | Observación menciona otra metodología | 🟡 | No |
| R24 | Fila con datos pero sin ID Estación | 🟠 | No |
| B01 | Diccionario nombres BD con entradas problemáticas | 🟡 | No |
| T01 | Plantilla: una fórmula y su lista de validación no coinciden | 🟡 | No |
| X01 | Columna de fórmula con un valor fijo | 🟠 | No |
| X02 | Valor cacheado de una fórmula desactualizado | 🟡 | No |

¹ Solo si está vacío o difiere en mayúsculas/espacios: un código distinto puede ser un renombre
deliberado para el informe. ² Solo si hay un valor mayoritario y la variante es un prefijo o un
typo claro (p. ej. «Punta del So» → «Punta del Sol»).

E14, E15, R21–R24, B01, T01, X01 y X02 son adicionales a la lista original.

### Agregar una regla

Cada regla es una función registrada que recibe el contexto (con los DataFrames y las uniones
ya hechas) y devuelve hallazgos. Créala en `auditor/reglas/` (en un módulo nuevo, impórtalo en
`auditor/reglas/__init__.py`):

```python
from ..hallazgos import Severidad, regla

@regla("R25", "Sexo fuera de la lista", Severidad.INFO)
def r25(ctx):
    for t, fila, u in ctx.iter_registros():       # u = unión rehecha (especie, clase, fecha, hora…)
        if t.valor(fila, "sexo") not in ("Macho", "Hembra", "Indeterminado"):
            yield ctx.hallazgo("R25", t, fila, "sexo", "Problema en español", "Sugerencia")
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
domésticas, distancia máxima de captura y horario de actividad de aves. El resto de los
parámetros (y sus valores por defecto) está en `auditor/config.py`; cualquiera se puede
sobrescribir desde el YAML.

## Pruebas

```
python -m pytest
python tests/aceptacion.py        # imprime la tabla «esperado vs detectado»
```

La planilla de prueba **no se versiona** (el repositorio es público y la planilla tiene
coordenadas de especies, datos del proyecto y nombres de personas). Cópiala a
`tests/datos/Formato_Planilla_Fauna_XPE_Externo_v4_version_1.xlsb.xlsx` o indica su ruta con
la variable `PLANILLA_XPE`; sin ella, las pruebas que la necesitan se saltan.

`tests/escenario.py` crea una copia de la planilla con los errores de la revisión manual que no
están en esa versión del archivo (PMF20, PMF29/PMF32, PMF35, Cururo «P», ID Final vacío,
«Punta del So»), para comprobar que las reglas los detectan.
