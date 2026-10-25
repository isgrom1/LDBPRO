"""Dibuja el ícono de la aplicación (icono.png e icono.ico) a partir de las mismas formas del SVG.

    python empaquetado/crear_icono.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

DESTINO = Path(__file__).resolve().parent.parent / "auditor" / "app" / "static"


def dibujar(lado: int = 512) -> Image.Image:
    e = lado / 64  # el SVG mide 64 × 64
    img = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    fondo = Image.new("RGBA", (lado, lado))
    d = ImageDraw.Draw(fondo)
    for y in range(lado):  # degradado vertical del verde
        t = y / lado
        d.line([(0, y), (lado, y)], fill=(int(63 + (31 - 63) * t), int(154 + (106 - 154) * t), int(100 + (64 - 100) * t)))
    mascara = Image.new("L", (lado, lado), 0)
    ImageDraw.Draw(mascara).rounded_rectangle([2 * e, 2 * e, 62 * e, 62 * e], radius=14 * e, fill=255)
    img.paste(fondo, (0, 0), mascara)
    d = ImageDraw.Draw(img)
    hoja = [(15, 47), (20, 33), (30, 22), (47, 16), (44, 30), (34, 42), (18, 47)]
    d.polygon([(x * e, y * e) for x, y in hoja], fill=(191, 227, 201))
    d.line([(17 * e, 46 * e), (41 * e, 23 * e)], fill=(31, 106, 64), width=round(2.4 * e))
    d.line([(48 * e, 46 * e), (54 * e, 52 * e)], fill=(20, 70, 42), width=round(4.5 * e))
    d.ellipse([31.5 * e, 29.5 * e, 50.5 * e, 48.5 * e], fill=(255, 255, 255), outline=(20, 70, 42), width=round(3 * e))
    d.line([(37 * e, 39.5 * e), (40 * e, 42.5 * e), (45.5 * e, 36.5 * e)], fill=(47, 125, 79), width=round(3 * e), joint="curve")
    return img


if __name__ == "__main__":
    img = dibujar()
    img.resize((256, 256), Image.LANCZOS).save(DESTINO / "icono.png")
    img.save(DESTINO / "icono.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("Ícono escrito en", DESTINO)
