"""Tests für release.sh — ohne echten Bau-, Notarisierungs- oder DMG-Lauf.

Geprüft wird nur, was ohne Zertifikat, Netz und Apple-Server prüfbar ist:

- Aufrufvertrag: ``--help`` endet mit 0, eine unbekannte Option mit 2, und in
  beiden Fällen wird nichts gebaut (kein Arbeitsordner, kein dist/).
- Bauplan im Skript: Die App muss ihr Ticket VOR dem DMG-Bau nachweisen
  (``stapler validate`` vor ``hdiutil create``), das Aufräumen darf nur das
  eigene Device aushängen (nie einen Pfad unter /Volumes), und nach
  /Applications wird nichts kopiert — release.sh installiert nie.

Wie die anderen Testdateien auch ohne pytest direkt ausführbar.
"""

from __future__ import annotations

import subprocess
import os
import plistlib
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RELEASE = REPO / "release.sh"
RELEASE_UTILS = REPO / "release-utils.sh"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(RELEASE), *args],
        cwd=REPO, capture_output=True, text=True, check=False,
    )


def test_syntax_ok() -> None:
    rc = subprocess.run(["bash", "-n", str(RELEASE)], check=False).returncode
    assert rc == 0, "release.sh hat einen Syntaxfehler."


def test_help_exit_0() -> None:
    res = _run("--help")
    assert res.returncode == 0, res.stderr
    assert "Installiert NIE" in res.stdout


def test_unbekannte_option_exit_2() -> None:
    # Ein Aufruffehler darf keinen Arbeitsordner anlegen. Verglichen wird
    # vorher/nachher statt auf "keiner da": Ein parallel laufender echter
    # Release-Lauf hat seinen eigenen Ordner, und der geht den Test nichts an.
    vorher = set(REPO.glob(".jpegdrop-release.*"))
    res = _run("--nonsense")
    assert res.returncode == 2, (res.stdout, res.stderr)
    assert "Unbekannte Option" in res.stderr
    assert set(REPO.glob(".jpegdrop-release.*")) == vorher, "Aufruffehler hat einen Arbeitsordner angelegt."


def test_testfeed_verhindert_release_vor_jedem_bau() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        shutil.copy(RELEASE, root / "release.sh")
        shutil.copy(RELEASE_UTILS, root / "release-utils.sh")
        (root / "build.sh").write_text('touch "built"\n')
        result = subprocess.run(["bash", str(root / "release.sh")], cwd=root,
                                env={**os.environ, "JPEGDROP_TEST_FEED_URL": "http://localhost/feed.xml"},
                                capture_output=True, text=True)
        assert result.returncode == 2, result.stderr
        assert "Testfeed" in result.stderr
        assert not (root / "built").exists()


def test_gebaute_test_app_wird_nicht_veroeffentlicht() -> None:
    with tempfile.TemporaryDirectory() as directory:
        app = Path(directory) / "JPEGdrop.app"
        contents = app / "Contents"
        contents.mkdir(parents=True)
        for config, expected in [
            ({"SUFeedURL": "https://example.com/appcast.xml"}, 0),
            ({"SUFeedURL": "http://localhost/feed.xml"}, 2),
            ({"SUFeedURL": "https://example.com/feed.xml", "JPEGdropTestUpdateFeed": True}, 2),
            ({}, 2),
        ]:
            with (contents / "Info.plist").open("wb") as target:
                plistlib.dump(config, target)
            result = subprocess.run(["bash", "-c", 'source "$1"; assert_release_update_configuration "$2"',
                                     "bash", str(RELEASE_UTILS), str(app)], capture_output=True, text=True)
            assert result.returncode == expected, result.stderr


def test_ticket_der_app_vor_dem_dmg_bau() -> None:
    quelle = RELEASE.read_text(encoding="utf-8")
    validate_app = quelle.index('xcrun stapler validate "$APP"')
    dmg_create = quelle.index("hdiutil create")
    assert validate_app < dmg_create, "App-Ticket muss vor dem DMG-Bau geprüft werden."
    # Das Ticket am DMG kommt zusätzlich, nicht stattdessen.
    assert 'stapler staple "$PENDING_DMG"' in quelle


def test_aufraeumen_haengt_nur_eigenes_device_aus() -> None:
    quelle = RELEASE.read_text(encoding="utf-8")
    assert 'hdiutil detach "$device"' in quelle
    assert 'hdiutil detach "/Volumes' not in quelle
    assert 'hdiutil detach "$MOUNT_DIR"' not in quelle
    assert 'DMG_ATTACH_STATE="attaching"' in quelle
    assert "device_from_hdiutil_info" in quelle


def test_release_paar_wird_bei_zweitem_fehler_zurueckgenommen() -> None:
    with tempfile.TemporaryDirectory() as td:
        wurzel = Path(td)
        (wurzel / "dist").mkdir()
        quelle_dmg = wurzel / "stage.dmg"
        quelle_sha = wurzel / "stage.sha256"
        quelle_dmg.write_bytes(b"dmg")
        quelle_sha.write_bytes(b"summe")
        ziel_dmg = wurzel / "dist/fertig.dmg"
        ziel_sha = wurzel / "fehlt/fertig.sha256"
        sperre = wurzel / "dist/.publish-lock"
        # Der bedingte Aufruf entspricht release.sh und schaltet Bash-errexit
        # im Funktionsrumpf aus. Kritische Schritte müssen selbst abbrechen.
        befehl = 'source "$1"; if ! publish_release_pair "$2" "$3" "$4" "$5" "$6"; then exit 23; fi'
        res = subprocess.run(
            ["bash", "-c", befehl, "bash", str(RELEASE_UTILS),
             str(quelle_dmg), str(quelle_sha), str(ziel_dmg), str(ziel_sha), str(sperre)],
            capture_output=True, text=True, check=False,
        )
        assert res.returncode != 0
        assert not ziel_dmg.exists(), "Ein einzelnes DMG blieb nach dem Fehler sichtbar."
        assert quelle_dmg.exists() and quelle_sha.exists()
        assert not sperre.exists()

        ziel_sha = ziel_dmg.with_suffix(".dmg.sha256")
        res = subprocess.run(
            ["bash", "-c", befehl, "bash", str(RELEASE_UTILS),
             str(quelle_dmg), str(quelle_sha), str(ziel_dmg), str(ziel_sha), str(sperre)],
            capture_output=True, text=True, check=False,
        )
        assert res.returncode == 0, res.stderr
        assert ziel_dmg.read_bytes() == b"dmg"
        assert ziel_sha.read_bytes() == b"summe"
        assert not quelle_dmg.exists() and not quelle_sha.exists()
        assert not sperre.exists()


def test_reservierung_folgt_keinem_verzeichnis_oder_symlink() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "stage.dmg"
        source.write_bytes(b"own artifact")
        folder = root / "existing-folder"
        folder.mkdir()
        link = root / "folder-link"
        link.symlink_to(folder, target_is_directory=True)
        for destination in (folder, link):
            result = subprocess.run(["/bin/bash", "-c", 'source "$1"; reserve_release_file "$2" "$3"',
                "bash", str(RELEASE_UTILS), str(source), str(destination)], capture_output=True, text=True)
            assert result.returncode != 0
            assert not list(folder.iterdir())
            assert source.read_bytes() == b"own artifact"


def test_kollisions_ruecknahme_erhaelt_eine_fremde_datei() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source, checksum = root / "stage.dmg", root / "stage.sha256"
        source.write_bytes(b"own artifact")
        checksum.write_bytes(b"own checksum")
        destination, sha_destination = root / "final.dmg", root / "final.sha256"
        lock = root / ".publish-lock"
        command = '''source "$1"
reserve_release_file() { printf 'foreign file' > "$2"; return 1; }
if ! publish_release_pair "$2" "$3" "$4" "$5" "$6"; then exit 23; fi
'''
        result = subprocess.run(["/bin/bash", "-c", command, "bash", str(RELEASE_UTILS),
            str(source), str(checksum), str(destination), str(sha_destination), str(lock)],
            capture_output=True, text=True)
        assert result.returncode == 23
        assert destination.read_bytes() == b"foreign file"
        assert source.exists() and checksum.exists()
        assert not sha_destination.exists()
        assert not lock.exists()



def test_installiert_nie() -> None:
    quelle = RELEASE.read_text(encoding="utf-8")
    # /Applications darf nur als Ziel der Verknüpfung im Image auftauchen.
    for zeile in quelle.splitlines():
        if "/Applications" in zeile and not zeile.lstrip().startswith("#"):
            assert zeile.strip() == 'ln -s /Applications "$MOUNT_DIR/Applications"', zeile
    assert "replaceItemAt" not in quelle
    assert "--no-finder-layout) FINDER_LAYOUT=0" in quelle


if __name__ == "__main__":
    fehler = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok    {name}")
            except AssertionError as exc:
                fehler += 1
                print(f"FAIL  {name}: {exc}")
    sys.exit(1 if fehler else 0)
