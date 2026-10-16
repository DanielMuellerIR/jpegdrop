"""Regressionstests für die Findings der gespeicherten Reviews.

Abgedeckt sind die Reviews vom 2026-07-21, 2026-08-02 (ab
``test_output_mode_respects_restrictive_umask``) und 2026-08-20.

Die Tests sind wie die bestehenden Testdateien direkt ohne pytest ausführbar.
Alle Bilddaten entstehen synthetisch in temporären Verzeichnissen.
"""

from __future__ import annotations

import contextlib
import errno
import io
import json
import os
import select
import stat
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import jpegdrop
from PIL import Image


def test_parallel_publication_never_reuses_target():
    """Gleichnamige parallele Drops müssen zwei vollständige Ziele erzeugen."""
    with tempfile.TemporaryDirectory(prefix="jpegdrop_test_parallel_") as td:
        directory = Path(td)
        sources = [directory / "gleich.png", directory / "gleich.bmp"]
        Image.new("RGB", (512, 512), (230, 20, 20)).save(sources[0])
        Image.new("RGB", (512, 512), (20, 20, 230)).save(sources[1])

        # Beide Speicheroperationen synchronisieren. Im fehlerhaften Stand haben
        # beide Worker davor denselben freien Zielpfad gewählt.
        original_save = Image.Image.save
        barrier = threading.Barrier(2)

        def synchronized_save(image, fp, *args, **kwargs):
            if Path(fp).parent == directory:
                barrier.wait(timeout=10)
            return original_save(image, fp, *args, **kwargs)

        Image.Image.save = synchronized_save
        try:
            codec = jpegdrop.get_codec("jpeg")
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(
                    pool.map(
                        lambda source: jpegdrop.process_one(source, codec, "hoch", "-klein"),
                        sources,
                    )
                )
        finally:
            Image.Image.save = original_save

        assert all(result["error"] is None for result in results), results
        targets = [Path(result["target"]) for result in results]
        assert len(set(targets)) == 2, f"Ziel doppelt vergeben: {targets}"
        assert all(target.is_file() and target.stat().st_size > 0 for target in targets)

        # Die zwei verschiedenfarbigen Eingaben müssen als zwei verschiedene
        # Ausgaben erhalten bleiben; ein Last-Writer-Wins wäre hier sichtbar.
        colors = []
        for target in targets:
            with Image.open(target) as output:
                colors.append(output.convert("RGB").getpixel((256, 256)))
        assert colors[0] != colors[1], f"Eine Ausgabe wurde überschrieben: {colors}"


def test_publication_falls_back_without_hardlinks():
    """Auch Dateisysteme ohne Hardlinks erhalten eine exklusive Zieldatei."""
    with tempfile.TemporaryDirectory(prefix="jpegdrop_test_publish_fallback_") as td:
        directory = Path(td)
        temp_output = directory / ".fertig.jpg"
        temp_output.write_bytes(b"synthetic encoded image")
        base = directory / "ziel.jpg"
        original_link = jpegdrop.os.link

        def unsupported_link(_source, _target):
            raise OSError(errno.EOPNOTSUPP, "hardlinks unsupported")

        jpegdrop.os.link = unsupported_link
        try:
            first = jpegdrop.publish_unique(temp_output, base)
            second = jpegdrop.publish_unique(temp_output, base)
        finally:
            jpegdrop.os.link = original_link

        assert first == base and second == directory / "ziel-2.jpg"
        assert first.read_bytes() == temp_output.read_bytes()
        assert second.read_bytes() == temp_output.read_bytes()


def test_non_utf8_filename_bytes_survive_name_fitting():
    """surrogateescape-Namen von Fremddateisystemen bleiben konvertierbar."""
    source = Path("/x/foto\udcff\udcfe.png")
    target = jpegdrop.target_base_path(source, jpegdrop.get_codec("jpeg"), "-klein")
    assert os.fsencode(target.name) == b"foto\xff\xfe.jpg"


def test_native_collision_keeps_the_complete_output_suffix():
    """Der Kollisionszähler darf ``-klein`` bei langen Namen nicht abschneiden."""
    with tempfile.TemporaryDirectory(prefix="jpegdrop_test_suffix_collision_") as td:
        source_stem = "n" * 251
        source = Path(td) / f"{source_stem}.png"
        base = jpegdrop.target_base_path(
            source, jpegdrop.get_codec("png"), "-klein"
        )
        base.touch()

        target = jpegdrop.unique_target(base, "-klein")

        assert len(target.name.encode()) <= jpegdrop.NAME_MAX
        assert target.name.endswith("-klein-2.png"), target.name


def test_cli_rejects_a_suffix_that_cannot_fit_a_stem():
    """Ein zu langes --suffix scheitert vor dem ersten Dateizugriff verständlich."""
    suffix = "x" * (jpegdrop.NAME_MAX - len(".jpg"))
    proc = subprocess.run(
        [sys.executable, str(Path(jpegdrop.__file__)), "--suffix", suffix, "fehlt.jpg"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert proc.returncode == 2, proc
    assert "--suffix ist zu lang" in proc.stderr, proc.stderr


def test_cli_rejects_suffix_without_collision_budget():
    """Ein Suffix ohne Platz fuer den Kollisionszaehler wird abgelehnt."""
    # 250 + 4 (".jpg") < 255, aber mit mindestens "-2" (2 Bytes) waeren es 256
    suffix = "x" * 250
    proc = subprocess.run(
        [sys.executable, str(Path(jpegdrop.__file__)), "--codec", "jpeg", "--suffix", suffix, "fehlt.jpg"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert proc.returncode == 2, proc
    assert "--suffix ist zu lang" in proc.stderr, proc.stderr


def test_cli_ignores_suffix_budget_for_foreign_source_codec():
    """PNG nach JPEG nutzt das Suffix nicht und darf daran nicht scheitern."""
    with tempfile.TemporaryDirectory(prefix="jpegdrop_test_foreign_suffix_") as td:
        source = Path(td) / "bild.png"
        Image.new("RGB", (8, 8), (10, 20, 30)).save(source)
        suffix = "x" * 250
        proc = subprocess.run(
            [sys.executable, str(Path(jpegdrop.__file__)), "--codec", "jpeg",
             "--json", "--suffix", suffix, str(source)],
            capture_output=True, text=True, timeout=20,
        )
        assert proc.returncode == 0, proc.stderr
        result = json.loads(proc.stdout)[0]
        assert Path(result["target"]).name == "bild.jpg", result


def test_cli_consecutive_conversions_with_boundary_suffix():
    """Zwei aufeinanderfolgende Konvertierungen mit Grenz-Suffix muessen kollisionsfrei gelingen."""
    with tempfile.TemporaryDirectory(prefix="jpegdrop_test_boundary_suffix_") as td:
        src = Path(td) / "a.jpg"
        Image.new("RGB", (10, 10), (255, 0, 0)).save(src, format="JPEG")
        # 255 - 4 (.jpg) - 2 (-2) - 1 (Stem "a") = 248
        boundary_suffix = "x" * 248
        cmd = [
            sys.executable,
            str(Path(jpegdrop.__file__)),
            "--codec",
            "jpeg",
            "--json",
            "--suffix",
            boundary_suffix,
            str(src),
        ]

        # 1. Konvertierung
        proc1 = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        assert proc1.returncode == 0, f"Lauf 1 fehlgeschlagen: {proc1.stderr}"
        res1 = json.loads(proc1.stdout)[0]
        assert res1["error"] is None
        target1 = Path(res1["target"])
        assert target1.is_file()
        assert len(target1.name.encode()) <= jpegdrop.NAME_MAX

        # 2. Konvertierung desselben Bildes -> Kollision muss -2 anhaengen
        proc2 = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        assert proc2.returncode == 0, f"Lauf 2 fehlgeschlagen: {proc2.stderr}"
        res2 = json.loads(proc2.stdout)[0]
        assert res2["error"] is None
        target2 = Path(res2["target"])
        assert target2.is_file()
        assert target2 != target1
        assert len(target2.name.encode()) <= jpegdrop.NAME_MAX
        assert target2.name.endswith("-2.jpg")


def test_unique_target_shortens_protected_suffix_if_budget_exhausted():
    """Uebersteigt selbst ohne Stem extra + suffix das Namensbudget, kuerzt unique_target das Suffix."""
    with tempfile.TemporaryDirectory(prefix="jpegdrop_test_unique_budget_") as td:
        suffix_250 = "x" * 250
        base = Path(td) / f"{suffix_250}.jpg"
        base.touch()
        cand = jpegdrop.unique_target(base, suffix_250)
        assert len(cand.name.encode()) <= jpegdrop.NAME_MAX
        assert cand.name.endswith("-2.jpg")
        assert not cand.exists()


def test_png_klein_256_is_palette_image_with_at_most_256_colors():
    """Das 256-Farben-Preset muss tatsächlich ein Paletten-PNG schreiben."""
    with tempfile.TemporaryDirectory(prefix="jpegdrop_test_palette_") as td:
        source = Path(td) / "gradient.bmp"
        image = Image.new("RGB", (512, 64))
        image.putdata(
            [
                ((x * 13 + y * 3) % 256, (x * 5 + y * 11) % 256, (x + y * 17) % 256)
                for y in range(image.height)
                for x in range(image.width)
            ]
        )
        image.save(source)

        result = jpegdrop.process_one(source, jpegdrop.get_codec("png"), "klein_256", "-klein")
        assert result["error"] is None, result
        with Image.open(result["target"]) as output:
            colors = output.getcolors(maxcolors=257)
            assert output.mode == "P", f"Erwartet Palettenmodus P, erhalten: {output.mode}"
            assert colors is not None and len(colors) <= 256, (
                f"Erwartet höchstens 256 Farben, erhalten: {colors and len(colors)}"
            )


def test_explicit_codec_uses_compatible_default_preset():
    """Ein Codec-Wechsel darf kein unpassendes Settings-Preset übernehmen."""
    with tempfile.TemporaryDirectory(prefix="jpegdrop_test_codec_switch_") as td:
        directory = Path(td)
        source = directory / "input.bmp"
        Image.new("RGB", (32, 32), (1, 2, 3)).save(source)

        old_dir, old_path = jpegdrop.SETTINGS_DIR, jpegdrop.SETTINGS_PATH
        jpegdrop.SETTINGS_DIR = directory / "settings"
        jpegdrop.SETTINGS_PATH = jpegdrop.SETTINGS_DIR / "settings.json"
        jpegdrop.SETTINGS_DIR.mkdir()
        jpegdrop.SETTINGS_PATH.write_text(
            json.dumps({"codec": "jpeg", "preset": "hoch"}), encoding="utf-8"
        )
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                exit_code = jpegdrop.main(["--codec", "png", "--json", str(source)])
        finally:
            jpegdrop.SETTINGS_DIR, jpegdrop.SETTINGS_PATH = old_dir, old_path

        assert exit_code == 0
        result = json.loads(output.getvalue())[0]
        assert result["codec"] == "png"
        assert result["preset"] == "verlustfrei", result


def test_settings_reject_non_object_and_non_string_fields():
    """JSON-Struktur und beide Felder müssen typfest validiert werden."""
    payloads = [
        "[]",
        "null",
        '{"codec": ["jpeg"], "preset": "hoch"}',
        '{"codec": "jpeg", "preset": ["hoch"]}',
    ]
    with tempfile.TemporaryDirectory(prefix="jpegdrop_test_settings_types_") as td:
        directory = Path(td)
        old_dir, old_path = jpegdrop.SETTINGS_DIR, jpegdrop.SETTINGS_PATH
        jpegdrop.SETTINGS_DIR = directory
        jpegdrop.SETTINGS_PATH = directory / "settings.json"
        try:
            for payload in payloads:
                jpegdrop.SETTINGS_PATH.write_text(payload, encoding="utf-8")
                settings = jpegdrop.load_settings()
                codec = jpegdrop.get_codec(settings["codec"])
                jpegdrop.get_preset(codec, settings["preset"])
        finally:
            jpegdrop.SETTINGS_DIR, jpegdrop.SETTINGS_PATH = old_dir, old_path


def test_gui_reports_first_concrete_image_error():
    """Die gemeinsame App-Kurzmeldung nennt bei Fehlern Datei und Ursache."""
    result = jpegdrop.empty_result(Path("kaputt.png"), "jpeg", "hoch")
    result["error"] = "UnidentifiedImageError: cannot identify image file"
    message = jpegdrop.batch_message([result])
    assert "kaputt.png" in message and "UnidentifiedImageError" in message, message


def test_status_menu_marks_current_codec():
    """Nach einem Codec-Wechsel bleibt genau ein Eintrag markiert."""
    from app import menu_app

    class Item:
        def __init__(self, name):
            self.name = name
            self.value = 0

        def representedObject(self):
            return self.name

        def setState_(self, value):
            self.value = value

    class Menu:
        def __init__(self, items):
            self.items = items

        def itemArray(self):
            return self.items

    class Delegate:
        _refresh_codec_submenu = menu_app.AppDelegate._refresh_codec_submenu

    delegate = Delegate()
    submenu = Menu([Item(codec.name) for codec in jpegdrop.CODECS.values()])
    delegate._codec_submenu = submenu
    original_load = menu_app.jpegdrop.load_settings
    try:
        for active in ("jpeg", "avif"):
            menu_app.jpegdrop.load_settings = lambda active=active: {"codec": active}
            delegate._refresh_codec_submenu()
            checked = [item.name for item in submenu.itemArray() if item.value == 1]
            assert checked == [active]
    finally:
        menu_app.jpegdrop.load_settings = original_load


def test_settings_codec_switch_selects_and_saves_compatible_preset():
    """Echte Cocoa-Popups: Codec-Wechsel wählt den Standard und speichert ihn."""
    from app import menu_app
    from unittest import mock
    from AppKit import NSApplication

    NSApplication.sharedApplication()
    controller = menu_app.SettingsWindowController.alloc().init()
    cases = [
        ({"codec": "jpeg", "preset": "mittel"}, "png", "verlustfrei"),
        ({"codec": "png", "preset": "klein_256"}, "jpeg", "hoch"),
        ({"codec": "jpeg", "preset": "auto"}, "png", "auto"),
        ({"codec": "jpeg", "preset": "hoch"}, "webp", "hoch"),
    ]
    try:
        for settings, target_codec, expected in cases:
            with mock.patch.object(jpegdrop, "load_settings", return_value=settings), \
                    mock.patch.object(jpegdrop, "save_settings") as save:
                controller._populate_from_settings()
                assert not controller._window.isVisible()
                controller._codec_popup.selectItemAtIndex_(controller._codec_names.index(target_codec))
                controller.codecChanged_(controller._codec_popup)
                title = controller._preset_popup.titleOfSelectedItem()
                assert title.split(" — ", 1)[0] == expected, title
                controller.saveAndClose_(None)
                save.assert_called_once_with({"codec": target_codec, "preset": expected})
    finally:
        if controller._window is not None:
            controller._window.orderOut_(None)


@contextlib.contextmanager
def _umask(value: int):
    """Setzt vorübergehend eine umask und leert den gemerkten Ausgabemodus.

    ``jpegdrop.publish_mode()`` liest die umask einmal pro Prozess und merkt
    sich das Ergebnis; für den Test muss dieser Zwischenspeicher mit umgesetzt
    werden.
    """
    previous_umask = os.umask(value)
    previous_mode = jpegdrop._PUBLISH_MODE
    jpegdrop._PUBLISH_MODE = None
    try:
        yield
    finally:
        os.umask(previous_umask)
        jpegdrop._PUBLISH_MODE = previous_mode


def test_output_mode_respects_restrictive_umask():
    """Eine restriktive umask darf nicht durch ein festes 0644 aufgehoben werden."""
    with tempfile.TemporaryDirectory(prefix="jpegdrop_test_umask_") as td:
        directory = Path(td)
        source = directory / "privat.png"
        Image.new("RGB", (32, 32), (9, 9, 9)).save(source)

        with _umask(0o077):
            result = jpegdrop.process_one(source, jpegdrop.get_codec("jpeg"), "hoch", "-klein")
            assert result["error"] is None, result
            mode = stat.S_IMODE(Path(result["target"]).stat().st_mode)
            assert mode == 0o600, f"Erwartet 0o600 bei umask 077, erhalten: {oct(mode)}"

        # Bei der üblichen umask bleibt die Ausgabe wie bisher für andere lokale
        # Programme lesbar — der Fix darf nichts unnötig einschnüren.
        with _umask(0o022):
            second = jpegdrop.process_one(source, jpegdrop.get_codec("jpeg"), "hoch", "-klein")
            assert second["error"] is None, second
            mode = stat.S_IMODE(Path(second["target"]).stat().st_mode)
            assert mode == 0o644, f"Erwartet 0o644 bei umask 022, erhalten: {oct(mode)}"


def test_output_mode_matches_on_hardlink_free_filesystems():
    """Der O_EXCL-Fallback muss denselben Modus liefern wie der Hardlink-Pfad."""
    with tempfile.TemporaryDirectory(prefix="jpegdrop_test_umask_fallback_") as td:
        source = Path(td) / "privat.png"
        Image.new("RGB", (32, 32), (9, 9, 9)).save(source)
        original_link = jpegdrop.os.link

        def unsupported_link(_source, _target):
            raise OSError(errno.EOPNOTSUPP, "hardlinks unsupported")

        jpegdrop.os.link = unsupported_link
        try:
            with _umask(0o077):
                result = jpegdrop.process_one(source, jpegdrop.get_codec("jpeg"), "hoch", "-klein")
        finally:
            jpegdrop.os.link = original_link

        assert result["error"] is None, result
        mode = stat.S_IMODE(Path(result["target"]).stat().st_mode)
        assert mode == 0o600, f"Erwartet 0o600 bei umask 077, erhalten: {oct(mode)}"


def test_output_has_its_final_mode_before_the_hardlink_is_visible():
    """Der atomare Hardlink darf nie den internen Temp-Modus 0600 veröffentlichen."""
    with tempfile.TemporaryDirectory(prefix="jpegdrop_test_mode_before_link_") as td:
        source = Path(td) / "sichtbar.png"
        Image.new("RGB", (32, 32), (8, 7, 6)).save(source)
        original_link = jpegdrop.os.link
        observed_modes: list[int] = []

        def observing_link(temp_path, target_path):
            observed_modes.append(stat.S_IMODE(Path(temp_path).stat().st_mode))
            return original_link(temp_path, target_path)

        jpegdrop.os.link = observing_link
        try:
            with _umask(0o022):
                result = jpegdrop.process_one(
                    source, jpegdrop.get_codec("jpeg"), "hoch", "-klein"
                )
        finally:
            jpegdrop.os.link = original_link

        assert result["error"] is None, result
        assert observed_modes == [0o644], [oct(mode) for mode in observed_modes]


def test_failed_final_chmod_publishes_nothing_and_cleans_temp():
    """Scheitert der Endmodus, bleibt weder ein Ziel noch eine versteckte Temp-Datei."""
    with tempfile.TemporaryDirectory(prefix="jpegdrop_test_chmod_failure_") as td:
        directory = Path(td)
        source = directory / "foto.png"
        Image.new("RGB", (32, 32), (1, 3, 5)).save(source)
        original_chmod = Path.chmod

        def failing_final_chmod(path, mode, *args, **kwargs):
            if path.name.startswith(jpegdrop.TEMP_PREFIX) and mode != 0o600:
                raise PermissionError(errno.EPERM, "chmod verweigert", path)
            return original_chmod(path, mode, *args, **kwargs)

        Path.chmod = failing_final_chmod
        try:
            result = jpegdrop.process_one(
                source, jpegdrop.get_codec("jpeg"), "hoch", "-klein"
            )
        finally:
            Path.chmod = original_chmod

        assert "PermissionError" in result["error"], result
        assert not (directory / "foto.jpg").exists()
        assert not list(directory.glob(f"{jpegdrop.TEMP_PREFIX}*"))


def test_failed_initial_temp_chmod_still_cleans_temp():
    """Auch ein Fehler direkt nach mkstemp läuft durch dessen finally-Aufräumung."""
    with tempfile.TemporaryDirectory(prefix="jpegdrop_test_initial_chmod_failure_") as td:
        directory = Path(td)
        source = directory / "foto.png"
        Image.new("RGB", (32, 32), (2, 4, 6)).save(source)
        original_chmod = Path.chmod

        def failing_temp_chmod(path, mode, *args, **kwargs):
            if path.name.startswith(jpegdrop.TEMP_PREFIX):
                raise PermissionError(errno.EPERM, "chmod verweigert", path)
            return original_chmod(path, mode, *args, **kwargs)

        Path.chmod = failing_temp_chmod
        try:
            result = jpegdrop.process_one(
                source, jpegdrop.get_codec("jpeg"), "hoch", "-klein"
            )
        finally:
            Path.chmod = original_chmod

        assert "PermissionError" in result["error"], result
        assert not list(directory.glob(f"{jpegdrop.TEMP_PREFIX}*"))


def test_sigterm_cleans_the_in_progress_temp_file():
    """Der Installer darf beim Stoppen einer Konvertierung keine Halbdatei hinterlassen."""
    with tempfile.TemporaryDirectory(prefix="jpegdrop_test_sigterm_") as td:
        directory = Path(td)
        source = directory / "langsam.png"
        Image.new("RGB", (64, 64), (10, 20, 30)).save(source)
        child_code = """
import sys
import time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import jpegdrop
from PIL import Image
source = Path(sys.argv[2])
original_save = Image.Image.save
def slow_save(image, target, *args, **kwargs):
    original_save(image, target, *args, **kwargs)
    print("TEMP_READY", flush=True)
    time.sleep(30)
Image.Image.save = slow_save
raise SystemExit(jpegdrop.main(["--codec", "jpeg", str(source)]))
"""
        proc = subprocess.Popen(
            [sys.executable, "-c", child_code, str(Path(jpegdrop.__file__).parent), str(source)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert proc.stdout is not None
            readable, _, _ = select.select([proc.stdout], [], [], 10)
            if not readable:
                raise AssertionError("Kindprozess meldet die angelegte Temp-Datei nicht")
            ready = proc.stdout.readline()
            assert ready.strip() == "TEMP_READY", ready
            assert list(directory.glob(f"{jpegdrop.TEMP_PREFIX}*"))
            proc.terminate()
            proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)

        assert proc.returncode == 128 + 15, proc.returncode
        assert not list(directory.glob(f"{jpegdrop.TEMP_PREFIX}*"))
        assert not (directory / "langsam.jpg").exists()


def test_long_source_name_still_converts():
    """Ein knapp gültiger Quellname darf nicht an der Zwischendatei scheitern.

    Der Zielname bleibt unter NAME_MAX (255 Bytes); früher wuchs die Zwischen-
    datei durch Ziel-Stem plus mkstemp-Zufallsanteil darüber hinaus und die
    Konvertierung brach mit ENAMETOOLONG ab.
    """
    with tempfile.TemporaryDirectory(prefix="jpegdrop_test_long_name_") as td:
        stem = "l" * 245
        source = Path(td) / f"{stem}.png"
        Image.new("RGB", (16, 16), (1, 2, 3)).save(source)
        assert len(source.name.encode()) <= 255

        result = jpegdrop.process_one(source, jpegdrop.get_codec("jpeg"), "hoch", "-klein")

        assert result["error"] is None, result
        target = Path(result["target"])
        assert target.name == f"{stem}.jpg"
        assert target.is_file() and target.stat().st_size > 0


def test_native_long_name_is_shortened_to_fit():
    """Ein maximal langer NATIVER Quellname bekommt zusätzlich das Suffix.

    251 Bytes Stem plus ``.png`` sind erlaubte 255 Bytes. Weil PNG nach PNG
    nativ ist, kommt ``-klein`` dazu — der Zielname wuchs damit auf 261 Bytes,
    und die Konvertierung brach mit ENAMETOOLONG ab. Der Stem muss also gekürzt
    werden.
    """
    with tempfile.TemporaryDirectory(prefix="jpegdrop_test_native_long_") as td:
        stem = "n" * 251
        source = Path(td) / f"{stem}.png"
        Image.new("RGB", (16, 16), (4, 5, 6)).save(source)
        assert len(source.name.encode()) == 255

        result = jpegdrop.process_one(source, jpegdrop.get_codec("png"), "verlustfrei", "-klein")

        assert result["error"] is None, result
        target = Path(result["target"])
        assert target.is_file() and target.stat().st_size > 0
        assert len(target.name.encode()) <= 255, target.name
        assert target.name.endswith("-klein.png"), target.name
        assert target != source, "die Quelle wurde überschrieben"


def test_collision_of_a_maximum_length_name_still_converts():
    """Auch der Kollisionszähler darf den Namen nicht über NAME_MAX schieben."""
    with tempfile.TemporaryDirectory(prefix="jpegdrop_test_long_collision_") as td:
        directory = Path(td)
        stem = "k" * 251
        source = directory / f"{stem}.bmp"
        Image.new("RGB", (16, 16), (7, 8, 9)).save(source)
        # Der reguläre Zielname ist schon belegt (255 Bytes) -> "-2" muss dazu.
        belegt = directory / f"{stem}.jpg"
        belegt.write_bytes(b"schon da")
        assert len(belegt.name.encode()) == 255

        result = jpegdrop.process_one(source, jpegdrop.get_codec("jpeg"), "hoch", "-klein")

        assert result["error"] is None, result
        target = Path(result["target"])
        assert target != belegt
        assert belegt.read_bytes() == b"schon da", "vorhandene Datei überschrieben"
        assert target.is_file() and target.stat().st_size > 0
        assert len(target.name.encode()) <= 255, target.name
        assert target.name.endswith("-2.jpg"), target.name


def test_publish_mode_keeps_the_process_umask_under_threads():
    """Zwei Threads dürfen sich das umask-Fenster nicht teilen.

    ``publish_mode()`` liest die umask, indem es sie kurz setzt und sofort
    zurückstellt. Ohne Sperre las der zweite Thread den bereits verstellten
    Wert als "Original" und stellte am Ende genau den wieder her — der Prozess
    behielt danach eine zu enge umask. Das ``sleep`` im Platzhalter reißt genau
    dieses Fenster auf, damit der Fall nicht vom Zufall abhängt.
    """
    import time

    echte_umask = os.umask
    vorher = echte_umask(0o022)
    gemerkt = jpegdrop._PUBLISH_MODE
    jpegdrop._PUBLISH_MODE = None

    def langsame_umask(value):
        ergebnis = echte_umask(value)
        time.sleep(0.05)
        return ergebnis

    ergebnisse: list[int] = []
    sammel_lock = threading.Lock()

    def ermitteln():
        modus = jpegdrop.publish_mode()
        with sammel_lock:
            ergebnisse.append(modus)

    os.umask = langsame_umask
    try:
        threads = [threading.Thread(target=ermitteln) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
    finally:
        os.umask = echte_umask
        jpegdrop._PUBLISH_MODE = gemerkt

    aktuell = echte_umask(vorher)
    assert aktuell == 0o022, (
        f"umask des Prozesses steht jetzt auf {oct(aktuell)} statt 0o22"
    )
    assert ergebnisse == [0o644, 0o644], [oct(m) for m in ergebnisse]


def test_write_only_output_mode_survives_the_copy_fallback():
    """Modus 0200 (nur schreiben) darf den Kopier-Fallback nicht blockieren.

    Bei umask 0477 bekommt die Ausgabe den Modus 0200. Wurde die Zwischendatei
    schon vor dem Veröffentlichen so gesetzt, konnte der Fallback für
    Dateisysteme ohne Hardlinks sie nicht mehr lesen (EACCES).
    """
    with tempfile.TemporaryDirectory(prefix="jpegdrop_test_writeonly_") as td:
        source = Path(td) / "nur-schreiben.png"
        Image.new("RGB", (32, 32), (3, 3, 3)).save(source)
        original_link = jpegdrop.os.link

        def unsupported_link(_source, _target):
            raise OSError(errno.EOPNOTSUPP, "hardlinks unsupported")

        jpegdrop.os.link = unsupported_link
        try:
            with _umask(0o477):
                result = jpegdrop.process_one(source, jpegdrop.get_codec("jpeg"), "hoch", "-klein")
        finally:
            jpegdrop.os.link = original_link

        assert result["error"] is None, result
        target = Path(result["target"])
        assert target.is_file()
        mode = stat.S_IMODE(target.stat().st_mode)
        assert mode == 0o200, f"Erwartet 0o200 bei umask 0477, erhalten: {oct(mode)}"
        assert result["out_bytes"] and result["out_bytes"] > 0, result


def test_palette_preset_is_not_reported_as_lossless():
    """png:klein_256 quantisiert — die Detailzeile darf nicht "verlustfrei" sagen."""
    with tempfile.TemporaryDirectory(prefix="jpegdrop_test_label_") as td:
        source = Path(td) / "farbverlauf.bmp"
        image = Image.new("RGB", (64, 64))
        image.putdata(
            [((x * 7) % 256, (y * 11) % 256, (x + y) % 256)
             for y in range(64) for x in range(64)]
        )
        image.save(source)

        result = jpegdrop.process_one(source, jpegdrop.get_codec("png"), "klein_256", "-klein")
        assert result["error"] is None, result
        line = jpegdrop.format_line(result)
        assert "256 Farben" in line, line
        assert "verlustfrei" not in line, line

        # Das echte verlustfreie Preset muss weiterhin so heißen, und die
        # Qualitätszahl verlustbehafteter Codecs bleibt unverändert.
        assert jpegdrop.quality_label("png", "verlustfrei", None) == "verlustfrei kodiert"
        assert jpegdrop.quality_label("jpeg", "hoch", 85) == "q85"
        assert jpegdrop.quality_label("jpeg", "fixed-q60", 60) == "q60"


if __name__ == "__main__":
    tests = [
        test_parallel_publication_never_reuses_target,
        test_publication_falls_back_without_hardlinks,
        test_non_utf8_filename_bytes_survive_name_fitting,
        test_native_collision_keeps_the_complete_output_suffix,
        test_cli_rejects_a_suffix_that_cannot_fit_a_stem,
        test_cli_rejects_suffix_without_collision_budget,
        test_cli_ignores_suffix_budget_for_foreign_source_codec,
        test_cli_consecutive_conversions_with_boundary_suffix,
        test_unique_target_shortens_protected_suffix_if_budget_exhausted,
        test_png_klein_256_is_palette_image_with_at_most_256_colors,
        test_explicit_codec_uses_compatible_default_preset,
        test_settings_reject_non_object_and_non_string_fields,
        test_gui_reports_first_concrete_image_error,
        test_status_menu_marks_current_codec,
        test_settings_codec_switch_selects_and_saves_compatible_preset,
        test_output_mode_respects_restrictive_umask,
        test_output_mode_matches_on_hardlink_free_filesystems,
        test_output_has_its_final_mode_before_the_hardlink_is_visible,
        test_failed_final_chmod_publishes_nothing_and_cleans_temp,
        test_failed_initial_temp_chmod_still_cleans_temp,
        test_sigterm_cleans_the_in_progress_temp_file,
        test_long_source_name_still_converts,
        test_native_long_name_is_shortened_to_fit,
        test_collision_of_a_maximum_length_name_still_converts,
        test_publish_mode_keeps_the_process_umask_under_threads,
        test_write_only_output_mode_survives_the_copy_fallback,
        test_palette_preset_is_not_reported_as_lossless,
    ]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  OK  {test.__name__}")
        except Exception as exc:
            print(f"  FAIL {test.__name__}: {type(exc).__name__}: {exc}")
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} Tests bestanden")
    sys.exit(failed)
