"""
Tests für den Bild-Kern (2026-07-12 ergänzt):

- Zielpfad-/Suffix-Logik (target_base_path/unique_target): nie die Quelle
  überschreiben, Kollisionen hochzählen.
- normalize_image / process_one: EXIF-Orientierung eingerechnet, Alpha auf
  Weiß flachgelegt, ICC-Profil gestrippt — der eigentliche Produktkern
  ("alle Metadaten weg, Bild bleibt korrekt"), bisher nur manuell geprüft.
"""

import sys
import tempfile
import unittest
from pathlib import Path

# jpegdrop.py liegt im Root des Repos, einen Ordner über diesem tests/-Verzeichnis
sys.path.insert(0, str(Path(__file__).parent.parent))

import jpegdrop
from PIL import Image, ImageCms


# ---------------------------------------------------------------------------
# Zielpfad-/Suffix-Logik
# ---------------------------------------------------------------------------

def test_target_path_foreign_extension():
    """Fremde Endung -> gleicher Name, neue Codec-Endung."""
    with tempfile.TemporaryDirectory(prefix="jpegdrop_test_core_") as td:
        d = Path(td)
        src = d / "foto.heic"
        src.touch()
        base = jpegdrop.target_base_path(src, jpegdrop.get_codec("jpeg"), "-klein")
        t = jpegdrop.unique_target(base)
        assert t == d / "foto.jpg", f"Erwartet foto.jpg, tatsächlich: {t}"


def test_target_path_native_extension_gets_suffix():
    """Quelle schon im Ziel-Codec -> Suffix anhängen (nie Quelle überschreiben)."""
    with tempfile.TemporaryDirectory(prefix="jpegdrop_test_core_") as td:
        d = Path(td)
        src = d / "foto.jpg"
        src.touch()
        base = jpegdrop.target_base_path(src, jpegdrop.get_codec("jpeg"), "-klein")
        t = jpegdrop.unique_target(base, "-klein")
        assert t == d / "foto-klein.jpg", f"Erwartet foto-klein.jpg, tatsächlich: {t}"


def test_target_path_uppercase_native():
    """.JPG (groß geschrieben) zählt als nativ -> Suffix statt Selbst-Treffer."""
    with tempfile.TemporaryDirectory(prefix="jpegdrop_test_core_") as td:
        d = Path(td)
        src = d / "FOTO.JPG"
        src.touch()
        base = jpegdrop.target_base_path(src, jpegdrop.get_codec("jpeg"), "-klein")
        t = jpegdrop.unique_target(base, "-klein")
        assert t.name == "FOTO-klein.jpg", f"Erwartet FOTO-klein.jpg, tatsächlich: {t.name}"


def test_unique_target_counts_up():
    """Existierende Ziele -> Zähler -2, -3, ... — nie überschreiben."""
    with tempfile.TemporaryDirectory(prefix="jpegdrop_test_core_") as td:
        d = Path(td)
        src = d / "foto.heic"
        src.touch()
        (d / "foto.jpg").touch()
        (d / "foto-2.jpg").touch()
        base = jpegdrop.target_base_path(src, jpegdrop.get_codec("jpeg"), "-klein")
        t = jpegdrop.unique_target(base)
        assert t == d / "foto-3.jpg", f"Erwartet foto-3.jpg, tatsächlich: {t}"


# ---------------------------------------------------------------------------
# normalize_image / process_one — Metadaten-Garantien
# ---------------------------------------------------------------------------

def test_exif_orientation_applied_and_stripped():
    """EXIF-Orientierung 6 (90° drehen) -> Pixel gedreht, EXIF im Ziel leer."""
    with tempfile.TemporaryDirectory(prefix="jpegdrop_test_core_") as td:
        src = Path(td) / "quer.jpg"
        img = Image.new("RGB", (400, 200), (10, 20, 30))
        exif = Image.Exif()
        exif[0x0112] = 6  # Orientation: "Rotate 90 CW"
        img.save(src, format="JPEG", exif=exif)

        result = jpegdrop.process_one(src, jpegdrop.get_codec("jpeg"), "hoch", "-t")
        assert result["error"] is None, f"Unerwarteter Fehler: {result['error']}"
        with Image.open(result["target"]) as out:
            assert out.size == (200, 400), (
                f"Orientierung nicht ins Pixelbild eingerechnet: {out.size}"
            )
            leftover = dict(out.getexif())
            assert leftover == {}, f"EXIF nicht gestrippt: {leftover}"


def test_alpha_flattened_on_white():
    """Halbtransparentes Rot auf weißem Grund -> Rosa, Modus RGB."""
    src_img = Image.new("RGBA", (10, 10), (255, 0, 0, 128))
    out = jpegdrop.normalize_image(src_img)
    assert out.mode == "RGB", f"Erwartet RGB, tatsächlich: {out.mode}"
    r, g, b = out.getpixel((5, 5))
    # 50%-Rot über Weiß: R bleibt 255, G/B landen bei ~127 (Rundung tolerieren)
    assert r == 255 and 120 <= g <= 135 and 120 <= b <= 135, (
        f"Alpha nicht korrekt auf Weiß flachgelegt: {(r, g, b)}"
    )


def test_cmyk_profile_is_applied_before_rgb_conversion():
    """Reales CMYK-ICC auf künstlichem TIFF; Ausgabe-Pixel müssen sRGB entsprechen."""
    profile_path = Path('/System/Library/ColorSync/Profiles/Generic CMYK Profile.icc')
    if not profile_path.is_file():
        raise unittest.SkipTest('macOS Generic-CMYK-Systemprofil fehlt')
    icc = profile_path.read_bytes()
    source = Image.new('CMYK', (8, 8), (100, 80, 30, 10))
    source.info['icc_profile'] = icc
    expected = ImageCms.profileToProfile(source, ImageCms.getOpenProfile(str(profile_path)),
                                         ImageCms.createProfile('sRGB'), outputMode='RGB')
    assert expected.getpixel((0, 0)) != source.convert('RGB').getpixel((0, 0))
    actual = jpegdrop.normalize_image(source)
    assert actual.tobytes() == expected.tobytes()
    assert actual.info == {}
    with tempfile.TemporaryDirectory(prefix='jpegdrop-cmyk-') as tmp:
        src = Path(tmp) / 'profile.tiff'
        source.save(src, icc_profile=icc)
        original = src.read_bytes()
        result = jpegdrop.process_one(src, jpegdrop.get_codec('png'), 'verlustfrei', '-test')
        assert result['error'] is None, result
        with Image.open(result['target']) as converted:
            assert converted.tobytes() == expected.tobytes()
            assert not converted.info.get('icc_profile')
        assert src.read_bytes() == original


def test_profile_conversion_preserves_alpha_before_flattening():
    source = Image.new('RGBA', (1, 1), (255, 0, 0, 128))
    source.info['icc_profile'] = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    actual = jpegdrop.normalize_image(source)
    assert actual.getpixel((0, 0)) == (255, 127, 127)
    assert actual.info == {}


def test_icc_profile_stripped():
    """Eingebettetes ICC-Profil -> im Ziel entfernt."""
    with tempfile.TemporaryDirectory(prefix="jpegdrop_test_core_") as td:
        src = Path(td) / "profil.jpg"
        icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        Image.new("RGB", (50, 50), (1, 2, 3)).save(src, format="JPEG", icc_profile=icc)

        result = jpegdrop.process_one(src, jpegdrop.get_codec("jpeg"), "hoch", "-t")
        assert result["error"] is None, f"Unerwarteter Fehler: {result['error']}"
        with Image.open(result["target"]) as out:
            assert out.info.get("icc_profile") is None, "ICC-Profil nicht gestrippt"


def test_png_rgb_trns_transparency_composited_on_white():
    """PNGs im RGB-Modus mit tRNS-Farbwert muessen auf Weiss flachgelegt werden."""
    with tempfile.TemporaryDirectory(prefix="jpegdrop_test_trns_rgb_") as td:
        src = Path(td) / "trns_rgb.png"
        im = Image.new("RGB", (8, 8), (0, 0, 0))
        im.putpixel((1, 0), (200, 50, 20))
        im.save(src, format="PNG", transparency=(0, 0, 0))

        # 1. normalize_image direkt
        with Image.open(src) as loaded:
            normalized = jpegdrop.normalize_image(loaded)
            assert normalized.getpixel((0, 0)) == (255, 255, 255)
            assert normalized.getpixel((1, 0)) == (200, 50, 20)
            assert normalized.info == {}

        # 2. Reale Konvertierung
        result = jpegdrop.process_one(src, jpegdrop.get_codec("png"), "verlustfrei", "-test")
        assert result["error"] is None, result
        with Image.open(result["target"]) as out:
            assert out.getpixel((0, 0)) == (255, 255, 255)
            assert out.getpixel((1, 0)) == (200, 50, 20)
            assert "transparency" not in out.info


def test_png_grayscale_trns_transparency_composited_on_white():
    """PNGs im Graustufen-(L)-Modus mit tRNS muessen auf Weiss flachgelegt werden."""
    with tempfile.TemporaryDirectory(prefix="jpegdrop_test_trns_l_") as td:
        src = Path(td) / "trns_l.png"
        im = Image.new("L", (8, 8), 0)
        im.putpixel((1, 0), 128)
        im.save(src, format="PNG", transparency=0)

        with Image.open(src) as loaded:
            normalized = jpegdrop.normalize_image(loaded)
            assert normalized.getpixel((0, 0)) == (255, 255, 255)
            assert normalized.getpixel((1, 0)) == (128, 128, 128)
            assert normalized.info == {}

        result = jpegdrop.process_one(src, jpegdrop.get_codec("jpeg"), "hoch", "-test")
        assert result["error"] is None, result
        with Image.open(result["target"]) as out:
            # JPEG-Kompression kann minimal schwanken, Weiß muss bei ~255 sein
            r, g, b = out.getpixel((0, 0))
            assert r >= 250 and g >= 250 and b >= 250
            r2, g2, b2 = out.getpixel((1, 0))
            assert 120 <= r2 <= 136 and 120 <= g2 <= 136 and 120 <= b2 <= 136


if __name__ == "__main__":
    # Direkt ausführbar ohne pytest
    tests = [
        test_target_path_foreign_extension,
        test_target_path_native_extension_gets_suffix,
        test_target_path_uppercase_native,
        test_unique_target_counts_up,
        test_exif_orientation_applied_and_stripped,
        test_alpha_flattened_on_white,
        test_icc_profile_stripped,
        test_cmyk_profile_is_applied_before_rgb_conversion,
        test_profile_conversion_preserves_alpha_before_flattening,
        test_png_rgb_trns_transparency_composited_on_white,
        test_png_grayscale_trns_transparency_composited_on_white,
    ]
    failed = 0
    skipped = 0
    for t in tests:
        try:
            t()
            print(f"  OK  {t.__name__}")
        except AssertionError as e:
            print(f"  FAIL {t.__name__}: {e}")
            failed += 1
        except unittest.SkipTest as e:
            # Ein Test meldet selbst, dass ihm hier eine Voraussetzung fehlt
            # (z. B. das CMYK-Systemprofil). Das ist kein Fehler: pytest wertet
            # es genauso als "übersprungen". Muss VOR "except Exception" stehen,
            # weil SkipTest ebenfalls eine Exception ist.
            print(f"  SKIP {t.__name__}: {e}")
            skipped += 1
        except Exception as e:
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    passed = len(tests) - failed - skipped
    print(f"\n{passed}/{len(tests)} Tests bestanden, {skipped} übersprungen")
    sys.exit(failed)
