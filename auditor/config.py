"""Carga de config.yaml sobre los valores por defecto."""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

POR_DEFECTO: dict = {
    "transecto": {
        "largo_min_m": 150,
        "largo_max_m": 250,
        "distancia_max_estacion_a_punto_medio_m": 150,
    },
    "ancho_transecto_m": {"Aves": 50, "Mamíferos": 20, "Reptiles": 4, "Anfibios": 4},
    "coordenadas": {"distancia_max_a_mediana_km": 5},
    "captura": {"distancia_max_m": 300},
    "domesticas": {
        "nombres_cientificos": [
            "Equus caballus", "Equus asinus", "Canis familiaris", "Canis lupus familiaris",
            "Felis catus", "Felis silvestris catus", "Bos taurus", "Capra hircus",
            "Ovis aries", "Gallus gallus", "Gallus gallus domesticus",
        ],
        "nombres_comunes": [
            "Caballo", "Caballo feral", "Perro", "Gato", "Gato doméstico", "Vaca",
            "Vaca feral", "Cabra", "Oveja", "Burro", "Gallina",
        ],
    },
    "aves": {"clase": "Aves", "horario_baja_actividad": ["12:00", "17:00"], "umbral_pct": 50},
    "horarios": {
        "nocturno": ["19:00", "06:00"],
        "metodologias_nocturnas": ["Playback anfibios"],
        "sherman_revision": ["06:00", "12:00"],
        # Nombres (como en la lista de validación) que usan R12 y R19
        "metodologia_sherman": "Trampas Sherman",
        "metodologia_camara": "Cámara trampa",
        "metodologia_transecto": "Transecto",
    },
    "fechas": {"tolerancia_dias": 3},
    "abundancia": {
        # R07: atípica si > Q3 + factor_iqr × max(IQR, 1), o > p99 cuando hay suficientes datos
        "min_registros_especie": 5,
        "factor_iqr": 3,
        "percentil": 99,
        "min_registros_percentil": 100,
    },
    "metodologias": {
        # Metodología (lista de 'validación datos') -> encabezado de la columna en Estaciones
        "columnas_estaciones": {
            "Transecto": "Transecto general",
            "Playback aves": "Playback Aves",
            "Playback anfibios": "Playback Anfibios",
            "Cámara trampa": "Cámara Trampa",
            "Trampas Sherman": "Trampas Sherman",
        },
        # R19: clases compatibles con cada metodología (las no listadas aceptan todo)
        "clases_permitidas": {
            "Playback aves": ["Aves"],
            "Playback anfibios": ["Anfibios"],
            "Trampas Sherman": ["Mamíferos", "Reptiles"],
            "Cámara trampa": ["Mamíferos", "Aves", "Reptiles"],
        },
        # R23: palabras en Observaciones que delatan otra metodología
        "palabras_clave": {
            "sherman": "Trampas Sherman",
            "camara": "Cámara trampa",
            "fototrampa": "Cámara trampa",
            "trampa camara": "Cámara trampa",
            "playback": "Playback",
        },
        # E11: textos que se convierten a 1 / 0
        "valores_si": ["si", "sí", "s", "x", "1", "yes", "true", "verdadero"],
        "valores_no": ["no", "n", "0", "false", "falso"],
    },
    # Nombres de clase equivalentes en nombres BD
    "clases_alias": {"Mammalia": "Mamíferos", "Reptilia": "Reptiles", "Amphibia": "Anfibios"},
    # R14: respaldo si no se puede leer la fórmula Directo/Indirecto de la planilla
    "directo_indirecto": {
        "Directo": ["Individuo", "Vocalización"],
        "Indirecto": [
            "Fecas", "Madriguera", "Cururera", "Plumas", "Muda", "Huellas", "Huesos",
            "Nido", "Registro de audio", "Egagrópila",
        ],
    },
    # R17: expresiones de duda en Observaciones / Fotos
    "texto_duda": [
        "?", "creo", "no sé", "no se", "revisar", "parece", "posible", "posiblemente",
        "duda", "confirmar", "quizá", "quizás", "quizas", "tal vez", "probable",
    ],
    # R04: nombres genéricos (expresiones regulares, sin distinguir mayúsculas)
    "nombres_genericos": [
        r"\bspp?\.?(?=\s|$)",
        r"\bsp\.\s*\d*$",
        r"\bindet\.?\b",
        r"\bindeterminad[oa]s?\b",
        r"\bno identificad[oa]s?\b",
        r"\bmorfoespecie\b",
    ],
    "fotos": {
        # R18: patrones válidos de código de foto (expresiones regulares)
        "patrones": [r"^TC_\d{5}$", r"^TimePhoto_(\d{8})_\d{6}$", r"^IMG_\d+$", r"^DSC_?\d+$"],
    },
    "puntaje": {
        "peso_registros": 0.7,
        "peso_estaciones": 0.3,
        "penalizacion_por_regla_critica": 2,
    },
    "estructura": {
        # Fila de encabezados esperada; se verifica al abrir y se informa si difiere.
        "Estaciones": 2,
        "Registros": 9,
        "Registros TA": 9,
        "nombres BD": 1,
    },
}


def _fusionar(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _fusionar(out[k], v)
        else:
            out[k] = v
    return out


def cargar_config(ruta: str | Path | None = None) -> dict:
    if ruta is None:
        por_omision = Path(__file__).resolve().parent.parent / "config.yaml"
        ruta = por_omision if por_omision.exists() else None
    if ruta is None:
        return copy.deepcopy(POR_DEFECTO)
    with open(ruta, encoding="utf-8") as f:
        datos = yaml.safe_load(f) or {}
    if not isinstance(datos, dict):
        raise ValueError(f"{ruta}: la configuración debe ser un diccionario YAML")
    return _fusionar(POR_DEFECTO, datos)
