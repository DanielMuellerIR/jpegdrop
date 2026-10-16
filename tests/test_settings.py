"""
Tests für load_settings/save_settings — insbesondere die Validierung.

Bug (2026-07-12): Bei ungültigem Preset fiel load_settings blind auf
DEFAULT_SETTINGS["preset"] ("hoch") zurück, ohne zu prüfen, ob das Preset
zum Codec passt. Bei codec=png gibt es kein "hoch" → jeder Aufruf ohne
Flags (auch jeder Droplet-Drop) brach mit SystemExit ab, bis man die
settings.json von Hand repariert hat. Der Fix wählt einen codec-gültigen
Fallback (erstes Preset des Codecs, falls "hoch" dort nicht existiert).
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

# jpegdrop.py liegt im Root des Repos, einen Ordner über diesem tests/-Verzeichnis
sys.path.insert(0, str(Path(__file__).parent.parent))

import jpegdrop


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _load_with_settings_file(content: str | None) -> dict:
    """Führt load_settings gegen eine temporäre settings.json aus.

    content=None bedeutet: Datei existiert nicht (Erststart-Fall).
    Biegt die Modul-Konstanten SETTINGS_DIR/SETTINGS_PATH vorübergehend um,
    damit die echten Benutzer-Settings unberührt bleiben.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="jpegdrop_test_settings_"))
    old_dir, old_path = jpegdrop.SETTINGS_DIR, jpegdrop.SETTINGS_PATH
    try:
        jpegdrop.SETTINGS_DIR = tmp_dir
        jpegdrop.SETTINGS_PATH = tmp_dir / "settings.json"
        if content is not None:
            jpegdrop.SETTINGS_PATH.write_text(content, encoding="utf-8")
        return jpegdrop.load_settings()
    finally:
        jpegdrop.SETTINGS_DIR, jpegdrop.SETTINGS_PATH = old_dir, old_path
        shutil.rmtree(tmp_dir)


def _assert_valid_pair(settings: dict) -> None:
    """Codec+Preset müssen zusammen gültig sein — sonst würde die CLI abbrechen."""
    codec = jpegdrop.get_codec(settings["codec"])
    # get_preset wirft SystemExit bei ungültigem Preset → Test schlägt fehl
    jpegdrop.get_preset(codec, settings["preset"])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_invalid_preset_falls_back_codec_valid():
    """Der Kernfall des Bugs: codec=png + ungültiges Preset.

    Vorher: Fallback "hoch" — das gibt es bei PNG nicht → SystemExit bei
    jedem Aufruf. Nachher: codec-gültiger Fallback (bei PNG: "auto").
    """
    s = _load_with_settings_file('{"codec": "png", "preset": "gibtsnicht"}')
    assert s["codec"] == "png", f"Codec 'png' ist gültig und muss erhalten bleiben: {s}"
    _assert_valid_pair(s)


def test_invalid_preset_jpeg_falls_back_hoch():
    """Bei JPEG ist "hoch" gültig — der bisherige Fallback bleibt erhalten."""
    s = _load_with_settings_file('{"codec": "jpeg", "preset": "quatsch"}')
    assert s == {"codec": "jpeg", "preset": "hoch"}, f"Erwartet jpeg/hoch, tatsächlich: {s}"


def test_invalid_codec_falls_back_defaults():
    """Unbekannter Codec → kompletter Rückfall auf die Defaults."""
    s = _load_with_settings_file('{"codec": "tiff", "preset": "mittel"}')
    assert s["codec"] == jpegdrop.DEFAULT_SETTINGS["codec"], f"Unerwarteter Codec: {s}"
    _assert_valid_pair(s)


def test_missing_file_returns_defaults():
    """Erststart ohne settings.json → Defaults, gültiges Paar."""
    s = _load_with_settings_file(None)
    assert s == dict(jpegdrop.DEFAULT_SETTINGS), f"Erwartet Defaults, tatsächlich: {s}"
    _assert_valid_pair(s)


def test_corrupt_json_returns_defaults():
    """Kaputte Datei (kein JSON) → Defaults statt Crash."""
    s = _load_with_settings_file("das ist kein json {{{")
    assert s == dict(jpegdrop.DEFAULT_SETTINGS), f"Erwartet Defaults, tatsächlich: {s}"


def test_save_load_roundtrip():
    """Gespeicherte gültige Settings kommen unverändert zurück."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="jpegdrop_test_settings_"))
    old_dir, old_path = jpegdrop.SETTINGS_DIR, jpegdrop.SETTINGS_PATH
    try:
        jpegdrop.SETTINGS_DIR = tmp_dir
        jpegdrop.SETTINGS_PATH = tmp_dir / "settings.json"
        jpegdrop.save_settings({"codec": "avif", "preset": "mittel"})
        s = jpegdrop.load_settings()
        assert s == {"codec": "avif", "preset": "mittel"}, f"Roundtrip verfälscht: {s}"
        # Atomares Schreiben: keine .tmp-Datei übrig
        leftovers = list(tmp_dir.glob("*.tmp"))
        assert not leftovers, f"Temp-Datei nicht weggeräumt: {leftovers}"
    finally:
        jpegdrop.SETTINGS_DIR, jpegdrop.SETTINGS_PATH = old_dir, old_path
        shutil.rmtree(tmp_dir)


if __name__ == "__main__":
    # Direkt ausführbar ohne pytest
    tests = [
        test_invalid_preset_falls_back_codec_valid,
        test_invalid_preset_jpeg_falls_back_hoch,
        test_invalid_codec_falls_back_defaults,
        test_missing_file_returns_defaults,
        test_corrupt_json_returns_defaults,
        test_save_load_roundtrip,
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
