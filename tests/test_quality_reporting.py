"""
Regressionstests für result["quality"]-Korrektheit in process_one.

Bug (2026-06-26): Bei non-auto Presets meldete process_one den auto_q-Wert
der adaptiven Kurve, schrieb aber die Preset-Qualität. Der Fix liest
result["quality"] jetzt aus save_kwargs.get("quality"), also dem tatsächlich
genutzten Wert.
"""

import io
import os
import sys
import tempfile
from pathlib import Path

# jpegdrop.py liegt im Root des Repos, einen Ordner über diesem tests/-Verzeichnis
sys.path.insert(0, str(Path(__file__).parent.parent))

import jpegdrop
from PIL import Image


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _make_test_image(width: int, height: int, suffix: str = ".jpg") -> Path:
    """Erzeugt ein temporäres RGB-Bild für Tests. Eindeutiger Dateiname via
    mkstemp — feste /tmp-Namen kollidierten bei parallelen Testläufen."""
    img = Image.new("RGB", (width, height), color=(200, 100, 50))
    fd, name = tempfile.mkstemp(prefix="jpegdrop_test_", suffix=suffix)
    os.close(fd)
    tmp = Path(name)
    img.save(tmp)
    return tmp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_quality_matches_preset_not_auto_curve():
    """
    Bei einem non-auto Preset muss result["quality"] mit der Preset-Tabelle
    übereinstimmen, NICHT mit der Auto-Kurve.

    Konkret: --codec jpeg --preset hoch auf einem 4000x3000-Bild.
    Die Preset-Tabelle liefert q=85.
    Die Auto-Kurve (pick_quality) gibt für max_edge=4000 einen anderen Wert.
    Vorher: result["quality"] == auto_q (falsch).
    Nachher: result["quality"] == 85 (richtig).
    """
    src = _make_test_image(4000, 3000)
    codec = jpegdrop.get_codec("jpeg")
    result = jpegdrop.process_one(src, codec, "hoch", "-test")

    # Aufräumen
    if result["target"]:
        Path(result["target"]).unlink(missing_ok=True)
    src.unlink(missing_ok=True)

    assert result["error"] is None, f"Unerwarteter Fehler: {result['error']}"

    # Preset "hoch" → quality=85 laut _jpeg_save_kwargs-Tabelle
    expected_quality = 85
    actual_quality   = result["quality"]

    # Auto-Kurve für max_edge=4000 — muss ANDERS sein als 85, sonst
    # ist der Test sinnlos (dann wäre beides gleich und der Bug nicht erkennbar)
    auto_q = jpegdrop.pick_quality(4000)
    assert auto_q != expected_quality, (
        f"Test-Voraussetzung verletzt: pick_quality(4000)=={auto_q} gleich "
        f"Preset-Qualität {expected_quality} — Test kann Bug nicht unterscheiden"
    )

    assert actual_quality == expected_quality, (
        f"Preset 'hoch' soll quality=85 melden, nicht auto_q={auto_q}. "
        f"Tatsächlich: {actual_quality}"
    )


def test_quality_auto_uses_adaptive_curve():
    """
    Bei preset='auto' muss result["quality"] die adaptive Kurve widerspiegeln.
    Für ein großes Bild (4000 px) liegt der Wert typischerweise unter 85.
    """
    src = _make_test_image(4000, 3000)
    codec = jpegdrop.get_codec("jpeg")
    result = jpegdrop.process_one(src, codec, "auto", "-test")

    if result["target"]:
        Path(result["target"]).unlink(missing_ok=True)
    src.unlink(missing_ok=True)

    assert result["error"] is None, f"Unerwarteter Fehler: {result['error']}"

    expected_auto_q = jpegdrop.pick_quality(4000)
    assert result["quality"] == expected_auto_q, (
        f"Auto-Preset soll quality=pick_quality(4000)={expected_auto_q} melden, "
        f"tatsächlich: {result['quality']}"
    )


def test_quality_png_is_none():
    """
    PNG ist verlustfrei — result["quality"] muss None sein.
    """
    png_src = _make_test_image(800, 600, suffix=".png")

    codec = jpegdrop.get_codec("png")
    result = jpegdrop.process_one(png_src, codec, "verlustfrei", "-test")

    if result["target"]:
        Path(result["target"]).unlink(missing_ok=True)
    png_src.unlink(missing_ok=True)

    assert result["error"] is None, f"Unerwarteter Fehler: {result['error']}"
    assert result["quality"] is None, (
        f"PNG-Preset 'verlustfrei' soll quality=None melden, "
        f"tatsächlich: {result['quality']}"
    )


def test_fixed_quality_preset_label():
    """
    process_one_with_fixed_quality (--quality N) muss preset='fixed-qN' melden,
    nicht 'auto'.
    """
    src = _make_test_image(1000, 800)
    result = jpegdrop.process_one_with_fixed_quality(src, 60, "-test")

    if result["target"]:
        Path(result["target"]).unlink(missing_ok=True)
    src.unlink(missing_ok=True)

    assert result["error"] is None, f"Unerwarteter Fehler: {result['error']}"
    assert result["preset"] == "fixed-q60", (
        f"Legacy-Pfad soll preset='fixed-q60' melden, tatsächlich: {result['preset']}"
    )
    assert result["quality"] == 60, (
        f"Legacy-Pfad soll quality=60 melden, tatsächlich: {result['quality']}"
    )


if __name__ == "__main__":
    # Direkt ausführbar ohne pytest
    tests = [
        test_quality_matches_preset_not_auto_curve,
        test_quality_auto_uses_adaptive_curve,
        test_quality_png_is_none,
        test_fixed_quality_preset_label,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  OK  {t.__name__}")
        except AssertionError as e:
            print(f"  FAIL {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} Tests bestanden")
    sys.exit(failed)
