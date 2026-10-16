#!/usr/bin/env python3
"""make_icon.py — aus einem KI-generierten Icon-Bild ein sauberes macOS-Icon machen.

Hintergrund: mflux/Z-Image  liefert eine quadratische Kachel auf neutralem
Hintergrund. macOS rundet App-Icons NICHT selbst (anders als iOS) — das Icon muss
seine Form (Squircle) mit transparenten Ecken selbst mitbringen. Dieses Tool:

  1. findet die Kachel über die FARBSÄTTIGUNG (die Kachel ist kräftig teal/blau;
     Hintergrund, eingebackener Schlagschatten und der weiße Rahmen sind grau/blass)
     -> schneidet exakt die farbige Kachel frei, ohne den Schatten,
  2. legt eine Squircle-Alpha-Maske an (abgerundete Ecken, weiche Kanten),
  3. schreibt ein 1024er RGBA-PNG als Master (assets/icon-1024.png).

Pure Pillow, keine Extra-Abhängigkeiten (numpy nicht nötig).

Warum Sättigung statt Farbdistanz: Der Schatten verläuft von kräftig (nah an der
Kachel) bis schwach und überlappt im Distanzmaß mit dem hellen Teal -> nicht sauber
trennbar. Über die Sättigung dagegen sind Schatten (grau), Hintergrund (beige) und
weißer Rahmen klar unterhalb der kräftigen Kachelfarben.

Aufruf:
  python tools/make_icon.py icon-src/icon_seed_42.png assets/icon-1024.png
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

OUT_SIZE = 1024
SS = 4                       # Supersampling für glatte Maskenkanten
CORNER_RADIUS = 0.2235       # Anteil der Kantenlänge (≈ Apple-Squircle-Optik)
SAT_THRESHOLD = 70           # HSV-Sättigung (0..255), ab der ein Pixel zur Kachel zählt


def tile_bbox(img: Image.Image) -> tuple[int, int, int, int]:
    """Bounding-Box der kräftig gesättigten Kachel (über HSV-S-Kanal)."""
    sat = img.convert("HSV").getchannel("S")
    mask = sat.point(lambda p: 255 if p > SAT_THRESHOLD else 0)
    bbox = mask.getbbox()
    return bbox or (0, 0, *img.size)


def squircle_mask(size: int, radius_frac: float) -> Image.Image:
    """Abgerundete Quadrat-Maske (Squircle-Optik). In `size`-Auflösung gezeichnet,
    der Aufrufer skaliert anschließend herunter -> glatte, kantengeglättete Maske."""
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=int(size * radius_frac), fill=255)
    return m


def make_icon(src_path: Path, out_path: Path) -> None:
    img = Image.open(src_path).convert("RGB")

    # 1) farbige Kachel freistellen (ohne Schatten/Hintergrund)
    x0, y0, x1, y1 = tile_bbox(img)
    tile = img.crop((x0, y0, x1, y1))

    # auf quadratische SS-Leinwand bringen (Kachel ist ~quadratisch)
    big = OUT_SIZE * SS
    tile = tile.resize((big, big), Image.LANCZOS)

    # 2) Squircle-Maske als Alpha. Die geraden Tile-Kanten reichen bis zum Rand
    #    (volle Farbe), nur die runden Ecken (dort liegt Beige) schneidet die Maske weg.
    sq = squircle_mask(big, CORNER_RADIUS)
    tile.putalpha(sq)

    # 3) auf Zielgröße runter (LANCZOS glättet die SS-Kanten)
    out = tile.resize((OUT_SIZE, OUT_SIZE), Image.LANCZOS)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.save(out_path)
    print(f"crop=({x0},{y0},{x1},{y1}) tile={x1-x0}x{y1-y0} -> {out_path} {out.size}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: make_icon.py <quelle.png> <ziel.png>")
    make_icon(Path(sys.argv[1]), Path(sys.argv[2]))
