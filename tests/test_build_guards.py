"""Tests für die Fail-closed-Absicherungen von build.sh und install.sh.

Es läuft hier kein echter Bau-, Installations- oder Notarisierungslauf:

- Die build.sh-Fälle rufen ausschließlich die reine Pfadprüfung mit temporären
  Schutzwurzeln auf. Auch ein defekter Guard startet niemals den Build.
- Der install.sh-Fall prüft nur die Gatekeeper-Auswertung: Er ruft die Funktion
  ``assess_gatekeeper`` aus dem Skript mit einem ``spctl``-Platzhalter auf, statt
  das Skript zu starten.

Hintergrund build.sh: Das Bauziel kommt aus ``JPEGDROP_APP_PATH``. Zeigt es nach
``/Applications``, würde dort ein Build eingesetzt, der die Notarisierung notfalls
still überspringt. Dorthin installiert allein ``install.sh``.

Hintergrund install.sh: ``spctl … | tail -2`` liefert den Status von ``tail``
(also immer 0) und verschluckt damit eine Gatekeeper-Ablehnung — der Lauf meldete
anschließend trotzdem "INSTALL OK".

Dazu die Fälle rund um den eingebetteten Interpreter (seit 2026-08-04): Das
ausgelieferte Bundle darf keinen Pfad des Build-Macs enthalten. Geprüft wird der
Bauplan in build.sh und, falls gerade ein gebautes Bundle danebenliegt, dieses
Bundle selbst.

Wie die anderen Testdateien auch ohne pytest direkt ausführbar.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "build.sh"
INSTALL = REPO / "install.sh"
RELEASE_UTILS = REPO / "release-utils.sh"


def _validate_path(ziel: str, *roots: str) -> subprocess.CompletedProcess:
    source = BUILD.read_text(encoding="utf-8")
    snippet = (
        "set -euo pipefail\n"
        + _shell_function(source, "resolve_path")
        + _shell_function(source, "validate_app_path")
        + 'validate_app_path "$@"\n'
    )
    return subprocess.run(
        ["bash", "-c", snippet, "guard-test", ziel, *roots],
        capture_output=True,
        text=True,
        timeout=10,
    )


def _assert_gesperrt(ziel: str, *roots: str) -> None:
    proc = _validate_path(ziel, *roots)
    assert proc.returncode == 2, f"{ziel}: rc={proc.returncode}\n{proc.stdout}{proc.stderr}"
    assert "als Bauziel gesperrt" in proc.stderr, proc.stderr


def test_applications_is_blocked():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        _assert_gesperrt(str(root / "JPEGdrop.app"), str(root))
        assert not list(root.iterdir())


def test_subdirectory_of_applications_is_blocked():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        _assert_gesperrt(str(root / "Testbau/JPEGdrop.app"), str(root))
        assert not list(root.iterdir())


def test_detour_via_parent_directory_is_blocked():
    """Umweg über ein nicht vorhandenes Unterverzeichnis von /Applications.

    Abgelehnt wird das jetzt schon eine Stufe frueher: ``..`` in einem noch
    nicht vorhandenen Teil des Pfades ist grundsaetzlich verboten (siehe den
    Test darunter). Wichtig bleibt, dass der Lauf mit Exit 2 endet und den Grund
    nennt.
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        proc = _validate_path(str(root / "x/../JPEGdrop.app"), str(root))
    assert proc.returncode == 2, f"rc={proc.returncode}\n{proc.stdout}{proc.stderr}"
    assert ("als Bauziel gesperrt" in proc.stderr
            or "'.' oder '..'" in proc.stderr), proc.stderr


def test_detour_from_an_unprotected_prefix_is_blocked():
    """Der Umweg ueber ein NICHT gesperrtes, noch nicht vorhandenes Verzeichnis.

    ``resolve_path`` loest nur den bereits existierenden Anfang des Pfades auf;
    im Rest blieb ein ``..`` stehen und die Sperre griff nicht — ``mkdir -p``
    loeste es spaeter aber sehr wohl auf und baute in ``/Applications``.
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        ziel = str(root / "neu/../JPEGdrop.app")
        proc = _validate_path(ziel, str(root / "Applications"))
        assert not list(root.iterdir())
    assert proc.returncode == 2, f"rc={proc.returncode}\n{proc.stdout}{proc.stderr}"
    assert "'.' oder '..'" in proc.stderr, proc.stderr


def test_system_is_blocked():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        _assert_gesperrt(str(root / "JPEGdrop.app"), str(root / "apps"), str(root))


def test_symlink_to_applications_is_blocked():
    with tempfile.TemporaryDirectory(prefix="jpegdrop-test-") as tmp:
        root = Path(tmp).resolve()
        protected = root / "protected"
        protected.mkdir()
        link = root / "apps-link"
        link.symlink_to(protected)
        _assert_gesperrt(str(link / "JPEGdrop.app"), str(protected))
        assert not list(protected.iterdir())


def test_non_app_path_is_still_rejected():
    """Die ältere Prüfung auf die .app-Endung darf nicht verloren gehen."""
    proc = _validate_path("kein-bundle")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "muss auf ein .app-Bundle zeigen" in proc.stderr


def test_default_system_roots_are_blocked_by_the_pure_validator():
    for target in ("/Applications/JPEGdrop.app", "/System/JPEGdrop.app"):
        _assert_gesperrt(target)


def test_allowed_path_is_returned_without_creating_it():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        target = root / "allowed/JPEGdrop.app"
        proc = _validate_path(str(target), str(root / "apps"), str(root / "system"))
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == str(target)
        assert not list(root.iterdir())


def _shell_function(source: str, name: str) -> str:
    """Eine top-level-Bash-Funktion samt schliessender Klammer ausschneiden."""
    start = source.index(f"{name}() {{")
    end = source.index("\n}\n", start) + len("\n}\n")
    return source[start:end]


def _swap_block(source: str) -> str:
    start = source.index('if [ -e "$APP_FINAL" ] || [ -L "$APP_FINAL" ]; then')
    end = source.index('WORKDIR=""', start) + len('WORKDIR=""')
    return source[start:end]


def test_resolve_path_propagates_an_unreadable_parent():
    """Scheitert cd/pwd, darf resolve_path keinen Pfad im Dateisystem-Root erfinden."""
    source = BUILD.read_text(encoding="utf-8")
    snippet = (
        "set -u\n"
        "cd() { return 1; }\n"
        f"{_shell_function(source, 'resolve_path')}\n"
        'resolve_path /private/tmp\n'
    )
    proc = subprocess.run(["bash", "-c", snippet], capture_output=True, text=True)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "/JPEGdrop.app" not in proc.stdout


def test_swap_replaces_a_broken_target_symlink():
    """Auch ein Symlink ohne Ziel wird als vorhandener Ziel-Eintrag beiseitegelegt."""
    source = BUILD.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="jpegdrop-swap-link-") as td:
        root = Path(td)
        final = root / "JPEGdrop.app"
        final.symlink_to(root / "fehlt")
        workdir = root / ".jpegdrop-build-test"
        app = workdir / final.name
        app.mkdir(parents=True)
        (app / "neu").write_text("neu", encoding="utf-8")
        snippet = (
            "set -euo pipefail\n"
            f'APP_FINAL="{final}"\nWORKDIR="{workdir}"\nAPP="{app}"\n'
            'ALT=""\nALT_DIR=""\nSWAP_DONE=0\n'
            f"{_swap_block(source)}\n"
        )
        proc = subprocess.run(["bash", "-c", snippet], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert final.is_dir() and (final / "neu").read_text() == "neu"
        assert not list(root.glob(".jpegdrop-alt.*"))


def test_failed_swap_restores_the_previous_bundle():
    """Scheitert die zweite Umbenennung, bleibt die Vorgaengerversion am Ziel."""
    source = BUILD.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="jpegdrop-swap-fail-") as td:
        root = Path(td)
        final = root / "JPEGdrop.app"
        final.mkdir()
        (final / "alt").write_text("alt", encoding="utf-8")
        workdir = root / ".jpegdrop-build-test"
        app = workdir / final.name
        app.mkdir(parents=True)
        (app / "neu").write_text("neu", encoding="utf-8")
        snippet = (
            "set -u\n"
            f'APP_FINAL="{final}"\nWORKDIR="{workdir}"\nAPP="{app}"\n'
            'ALT=""\nALT_DIR=""\nSWAP_DONE=0\nmv_aufrufe=0\n'
            'mv() { mv_aufrufe=$((mv_aufrufe + 1)); '
            'if [ "$mv_aufrufe" -eq 2 ]; then return 1; fi; command mv "$@"; }\n'
            f"{_swap_block(source)}\n"
        )
        proc = subprocess.run(["bash", "-c", snippet], capture_output=True, text=True)
        assert proc.returncode != 0, proc.stdout
        assert (final / "alt").exists(), proc.stderr
        assert (final / "alt").read_text() == "alt"
        assert "wiederhergestellt" in proc.stderr, proc.stderr
        assert not list(root.glob(".jpegdrop-alt.*"))


def test_exit_trap_restores_bundle_and_cleans_all_temp_directories():
    """SIGTERM zwischen den Umbenennungen darf weder App noch Temp-Verzeichnisse verlieren."""
    source = BUILD.read_text(encoding="utf-8")
    cleanup = _shell_function(source, "aufraeumen")
    with tempfile.TemporaryDirectory(prefix="jpegdrop-trap-") as td:
        root = Path(td)
        final = root / "JPEGdrop.app"
        final.mkdir()
        (final / "alt").write_text("alt", encoding="utf-8")
        workdir = root / ".jpegdrop-build-test"
        workdir.mkdir()
        temp_dirs = [root / name for name in ("ent", "pbs", "icon", "zip")]
        for path in temp_dirs:
            path.mkdir()
            (path / "rest").touch()
        snippet = (
            "set -u\n"
            f'APP_FINAL="{final}"\nWORKDIR="{workdir}"\nAPP="$WORKDIR/JPEGdrop.app"\n'
            f'ENTITLEMENTS_DIR="{temp_dirs[0]}"\nPBS_TMP="{temp_dirs[1]}"\n'
            f'ICON_TMP="{temp_dirs[2]}"\nNZIP_DIR="{temp_dirs[3]}"\n'
            'ENTITLEMENTS=""\nALT_DIR="$(mktemp -d "'
            f'{root}/.jpegdrop-alt.XXXXXX")"\n'
            'ALT="$ALT_DIR/JPEGdrop.app"\nSWAP_DONE=0\n'
            f"{cleanup}\ntrap aufraeumen EXIT\ntrap 'exit 143' TERM\n"
            'mv "$APP_FINAL" "$ALT"\nkill -TERM $$\n'
        )
        proc = subprocess.run(["bash", "-c", snippet], capture_output=True, text=True)
        assert proc.returncode != 0
        assert (final / "alt").exists(), proc.stderr
        assert (final / "alt").read_text() == "alt"
        assert not workdir.exists()
        assert all(not path.exists() for path in temp_dirs)
        assert not list(root.glob(".jpegdrop-alt.*"))


def test_bundle_path_scan_fails_closed_on_a_grep_error():
    """Ein grep-Lesefehler darf die Abschlusspruefung nicht als sauber werten."""
    source = BUILD.read_text(encoding="utf-8")
    start = source.index("grep_rc=0")
    end = source.index("# --- Fertiges Bundle", start)
    guard = source[start:end]
    with tempfile.TemporaryDirectory(prefix="jpegdrop-grep-guard-") as td:
        root = Path(td)
        app = root / "JPEGdrop.app"
        app.mkdir()
        (app / "treffer").write_text("MAC-PFAD", encoding="utf-8")
        unreadable = app / "nicht-lesbar"
        unreadable.write_text("x", encoding="utf-8")
        unreadable.chmod(0)
        snippet = (
            "set -u\n"
            f'HERE="MAC-PFAD"\nAPP="{app}"\nWORKDIR="{root}"\n'
            f"{guard}\n"
        )
        proc = subprocess.run(["bash", "-c", snippet], capture_output=True, text=True)
        assert proc.returncode != 0, proc.stdout
        assert "nicht vollstaendig" in proc.stderr, proc.stderr


# --------------------------------------------------------------------------
# Eingebetteter Interpreter: kein Pfad des Build-Macs im Bundle
# --------------------------------------------------------------------------

def test_nativer_starter_verwendet_bundle_relative_laufzeit():
    """Bauvertrag: nativer Hauptprozess und dynamische Laufzeit im eigenen Bundle.

    Die tatsächliche NSBundle-Zuordnung und Sparkle-Neustartprobe sind separate
    Integrationstests; diese Wache schützt die nötigen Linker-/Isolationsoptionen.
    """
    source = BUILD.read_text(encoding="utf-8")
    launcher = (REPO / "app/launcher.m").read_text(encoding="utf-8")
    assert '"$HERE/app/launcher.m"' in source
    assert '-Wl,-rpath,@executable_path/../Resources/python/lib' in source
    assert 'config.write_bytecode = 0' in launcher
    assert 'PyConfig_InitIsolatedConfig' in launcher
    assert 'NSBundle.mainBundle' in launcher
    assert 'JPEGDROP_VENV_PY' in launcher
    assert not re.search(r'^rm .*libpython', source, re.MULTILINE)


def test_interpreter_pruefsumme_ist_gepinnt():
    """Das heruntergeladene Interpreter-Archiv wird gegen eine feste Pruefsumme
    geprueft, und ein Fehlschlag bricht ab (fail-closed)."""
    quelle = BUILD.read_text(encoding="utf-8")
    assert "PBS_SHA256=" in quelle, "Pruefsumme des Interpreter-Archivs fehlt."
    # Genau 64 Hex-Zeichen — ein leerer oder verkuerzter Wert wuerde die
    # Pruefung wirkungslos machen.
    zeile = next(z for z in quelle.splitlines() if z.startswith("PBS_SHA256="))
    wert = zeile.split("=", 1)[1].strip().strip('"')
    assert len(wert) == 64 and all(c in "0123456789abcdef" for c in wert), zeile
    assert "Prüfsumme des Interpreter-Archivs passt nicht" in quelle, (
        "Der Abbruch bei falscher Pruefsumme fehlt."
    )


def test_python_nebenversion_kommt_aus_pbs_python():
    """Die Nebenversion (heute 3.14) steht nur in PBS_PYTHON, nicht in den Pfaden.

    Nacht-Review 2026-09-16: Ausduennen, libpython-Pfade und die
    Signatur-Sonderregel nannten ``python3.14`` fest im Text. Nach einem Wechsel
    von PBS_PYTHON haette ``rm -rf`` die Pfade still verfehlt und die App waere
    unbemerkt rund 45 MB groesser geworden. Kommentare zaehlen nicht mit.
    """
    quelle = BUILD.read_text(encoding="utf-8")
    code = [z for z in quelle.splitlines() if not z.lstrip().startswith("#")]
    fest = [z.strip() for z in code if re.search(r"python3\.\d+", z)]
    assert not fest, "Feste Python-Nebenversion in build.sh: " + " | ".join(fest)

    # Die Ableitung wirklich von bash auswerten lassen, statt nur Text zu vergleichen.
    zeile = next(z for z in code if z.startswith("PBS_PYMINOR="))
    for voll, erwartet in (("3.14.6", "3.14"), ("3.15.0", "3.15"), ("3.9.20", "3.9")):
        proc = subprocess.run(
            ["bash", "-c", f'PBS_PYTHON="{voll}"; {zeile}; printf %s "$PBS_PYMINOR"'],
            capture_output=True,
            text=True,
            check=True,
        )
        assert proc.stdout == erwartet, f"{voll} -> {proc.stdout!r}, erwartet {erwartet!r}"

    assert "Interpreter-Archiv passt nicht zu PBS_PYTHON" in quelle, (
        "Der Abbruch bei unpassenden Ordnernamen im Interpreter-Archiv fehlt."
    )


def test_bauskript_prueft_ganzes_bundle_auf_mac_pfad():
    """build.sh muss am Ende das GANZE Bundle absuchen, Binaerdateien inklusive.

    Beides ist am 2026-08-04 schiefgegangen: Eine engere Fassung sah nur drei
    Textdateien an und uebersah die .pyc-Dateien des Interpreters, in denen
    Python den Quellpfad einbackt. Und ein als `grep` installiertes ugrep, das
    Binaerdateien ohne -a stillschweigend ueberspringt — die Pruefung meldete
    "sauber", obwohl der Pfad drinstand.
    """
    quelle = BUILD.read_text(encoding="utf-8")
    assert '/usr/bin/grep -raF -l "$HERE" "$APP"' in quelle, (
        "Die Abschlusspruefung muss das ganze Bundle mit /usr/bin/grep -raF "
        "absuchen (-a, sonst bleiben Binaerdateien ungeprueft)."
    )


def test_bauskript_kompiliert_ohne_mac_pfad_vor():
    """Die .pyc-Dateien duerfen den Bau-Pfad nicht tragen.

    Python schreibt den Quellpfad in jede .pyc. ``compileall -s/-p`` ersetzt ihn;
    ohne diese Schalter stuende der Ort dieses Macs im ausgelieferten Bundle.
    """
    quelle = BUILD.read_text(encoding="utf-8")
    assert "compileall" in quelle, "Vorkompilieren fehlt in build.sh."
    assert '-s "$APP" -p "/JPEGdrop.app"' in quelle, (
        "compileall braucht -s/-p, sonst steht der Bau-Pfad in jeder .pyc."
    )
    assert '"$BUNDLE_PY" -I -B -c' in quelle, (
        "Der Selbsttest muss isoliert und ohne Bytecode-Schreiben laufen, "
        "sonst können externe Pakete geladen oder Bau-Pfade eingebrannt werden."
    )


def _bundle_dateien_mit(app: Path, nadel: str) -> list[Path]:
    """Alle Dateien im Bundle, die ``nadel`` enthalten — Binaerdateien
    eingeschlossen (deshalb byteweise statt ueber grep)."""
    roh = nadel.encode("utf-8")
    treffer = []
    for pfad in app.rglob("*"):
        if not pfad.is_file() or pfad.is_symlink():
            continue
        try:
            if roh in pfad.read_bytes():
                treffer.append(pfad)
        except OSError:
            continue
    return treffer


def test_gebautes_bundle_ohne_mac_pfad():
    """Falls ein gebautes Bundle daneben liegt: nirgends eine Fundstelle des
    Repo-Pfads — auch nicht in einer .pyc oder sonst einer Binaerdatei.

    Kein Bundle da (frischer Clone, CI ohne Bau) -> Test wird uebersprungen.
    """
    app = REPO / "JPEGdrop.app"
    if not app.is_dir():
        return
    treffer = _bundle_dateien_mit(app, str(REPO))
    assert not treffer, (
        "Diese Bundle-Dateien enthalten den Repo-Pfad: "
        + ", ".join(str(p.relative_to(app)) for p in treffer[:5])
    )
    # Und der Interpreter muss wirklich mitgeliefert sein, sonst waere die App
    # zwar pfadfrei, aber nicht startfaehig.
    assert (app / "Contents" / "Resources" / "python" / "bin" / "python3").exists(), (
        "Im Bundle fehlt der eingebettete Interpreter."
    )


def test_gebautes_bundle_ohne_symlink_nach_draussen():
    """Ein Symlink aus dem Bundle heraus wuerde codesign als 'invalid
    destination' ablehnen — und waere wieder ein Pfad dieses Macs."""
    app = REPO / "JPEGdrop.app"
    if not app.is_dir():
        return
    hinaus = [
        p for p in app.rglob("*")
        if p.is_symlink() and os.readlink(p).startswith("/")
    ]
    assert not hinaus, f"absolute Symlinks im Bundle: {hinaus[:5]}"


# --------------------------------------------------------------------------
# install.sh: welche laufenden Instanzen vor dem Austausch beendet werden
# --------------------------------------------------------------------------

def _stop_snippet(prozessliste: str, pfade: list[str], *,
                  bleibt_am_leben: bool = False,
                  kill_verweigert: bool = False,
                  timeout_sekunden: str = "1") -> str:
    """Bash-Schnipsel bauen, das ``stop_repo_instances`` isoliert ausfuehrt.

    Das Skript wird nicht ausgefuehrt: Die Funktion wird herausgeschnitten,
    ``ps`` liefert die uebergebene Liste, und ``kill`` beendet nichts, sondern
    schreibt nur die Nummer auf die Ausgabe. Der Platzhalter beantwortet auch
    die Lebendprobe ``kill -0``: normalerweise mit "Prozess ist weg", mit
    ``bleibt_am_leben`` dagegen mit "laeuft noch" — so laesst sich die Frist
    pruefen, ohne echte Prozesse zu starten.
    """
    quelle = INSTALL.read_text(encoding="utf-8")
    start = quelle.index('STOP_TIMEOUT="${JPEGDROP_STOP_TIMEOUT:-10}"')
    funktionsstart = quelle.index("stop_repo_instances() {", start)
    ende = quelle.index("\n}\n", funktionsstart) + len("\n}\n")
    stop_code = quelle[start:ende]
    argumente = " ".join(f'"{p}"' for p in pfade)
    lebt = bleibt_am_leben or kill_verweigert
    lebend_status = "return 0" if lebt else "return 1"
    kill_status = (
        'echo "Operation not permitted" >&2; return 1'
        if kill_verweigert
        else 'printf "%s\\n" "$1" >> "$KILL_LOG"; return 0'
    )
    return (
        "set -euo pipefail\n"
        f'JPEGDROP_STOP_TIMEOUT="{timeout_sekunden}"\nexport JPEGDROP_STOP_TIMEOUT\n'
        'KILL_LOG="$(mktemp)"\ntrap \'rm -f "$KILL_LOG"\' EXIT\n'
        f"ps() {{ if [ \"${{1:-}}\" = \"-p\" ]; then {lebend_status}; fi; "
        f"cat <<LISTE\n{prozessliste}\nLISTE\n}}\n"
        'kill() { if [ "$1" = "-0" ]; then ' + lebend_status + '; fi; '
        + kill_status + '; }\n'
        f"{stop_code}\n"
        f"rc=0\nstop_repo_instances {argumente} || rc=$?\n"
        'sed \'s/^/GETOETET:/\' "$KILL_LOG"\nexit "$rc"\n'
    )


def _stop_instances(prozessliste: str, pfade: list[str]) -> list[str]:
    """Normalfall: Die getroffenen Prozesse enden nach dem Signal."""
    snippet = _stop_snippet(prozessliste, pfade)
    proc = subprocess.run(["bash", "-c", snippet], capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return [z.split(":", 1)[1] for z in proc.stdout.splitlines()
            if z.startswith("GETOETET:")]


INSTALLIERT = "/Applications/JPEGdrop.app/Contents/Resources/app/menu_app.py"
IM_REPO = "/Users/x/git/jpegdrop/app/menu_app.py"


def test_installierte_instanz_wird_beendet():
    """Die App in /Applications laeuft unter ihrem eigenen Pfad im Bundle.

    Vor dem eingebetteten Interpreter lief sie unter dem Repo-Pfad; wer nur den
    weitersucht, tauscht das Bundle unter einer laufenden App aus.
    """
    liste = (
        "  501 python3 "
        f"/Applications/JPEGdrop.app/Contents/Resources/python/bin/python3 {INSTALLIERT}"
    )
    assert _stop_instances(liste, [INSTALLIERT, IM_REPO]) == ["501"]


def test_entwicklungsinstanz_aus_dem_repo_wird_beendet():
    liste = f"  502 python /Users/x/git/jpegdrop/.venv/bin/python {IM_REPO}"
    assert _stop_instances(liste, [INSTALLIERT, IM_REPO]) == ["502"]


def test_entwicklungsinstanz_unter_pfad_mit_leerzeichen_wird_beendet():
    # comm liefert auf macOS den vollständigen Interpreterpfad; ucomm nur den
    # Namen, den stop_repo_instances vor der Kommandozeile getrennt lesen kann.
    assert "pid=,ucomm=,command=" in INSTALL.read_text(encoding="utf-8")
    script = "/Users/x/JPEG drop/app/menu_app.py"
    liste = f"  503 python /Users/x/JPEG drop/.venv/bin/python {script}"
    assert _stop_instances(liste, [script]) == ["503"]


def test_fremde_prozesse_bleiben_verschont():
    """Nur ganze Argumente zaehlen — ein Editor oder grep mit dem Pfad im Text
    darf nicht beendet werden."""
    liste = "\n".join([
        f"  601 vim /usr/bin/vim {INSTALLIERT}",
        f"  602 less /usr/bin/less {INSTALLIERT}",
        f"  603 git /usr/bin/git diff {INSTALLIERT}",
        f"  604 grep /usr/bin/grep -n foo {INSTALLIERT}",
        "  605 python3 /usr/bin/python3 /ganz/woanders/app/menu_app.py",
    ])
    assert _stop_instances(liste, [INSTALLIERT, IM_REPO]) == []


def test_fremde_python_werkzeuge_mit_skript_als_argument_bleiben_verschont():
    """Fremde Python-Werkzeuge, die JPEGdrop-Quelltext lediglich als Eingabedatei
    verarbeiten, duerfen nicht beendet werden."""
    liste = "\n".join([
        f"  502 python3 /tmp/analyze.py {INSTALLIERT}",
        f"  503 python3 /usr/bin/python3 /tmp/linter.py {IM_REPO}",
        f"  504 python3 /usr/bin/python3 -u /tmp/checker.py --target {INSTALLIERT}",
        f"  505 python3 /usr/bin/python3 -c 'import sys; print(sys.argv)' {INSTALLIERT}",
        f"  506 python3 /usr/bin/python3 -m unittest {INSTALLIERT}",
    ])
    assert _stop_instances(liste, [INSTALLIERT, IM_REPO]) == []


def test_jede_instanz_nur_einmal_beendet():
    """Ein Prozess, auf den mehrere gesuchte Pfade passen koennten, darf nur ein
    einziges kill bekommen (das `break` in der Schleife)."""
    liste = f"  701 python3 python3 {INSTALLIERT}"
    assert _stop_instances(liste, [INSTALLIERT, INSTALLIERT]) == ["701"]


def test_installationsskript_beendet_sich_nicht_selbst():
    liste = f"  $$ python3 python3 {INSTALLIERT}"
    assert _stop_instances(liste, [INSTALLIERT]) == []


def test_nicht_beendbarer_prozess_bricht_vor_dem_austausch_ab():
    liste = f"  702 python3 python3 {INSTALLIERT}"
    snippet = _stop_snippet(liste, [INSTALLIERT], kill_verweigert=True)
    proc = subprocess.run(["bash", "-c", snippet], capture_output=True,
                          text=True, timeout=20)
    assert proc.returncode != 0, proc.stdout
    assert "konnte nicht beendet werden" in proc.stderr, proc.stderr
    assert "702" in proc.stderr, proc.stderr


CLI_INSTALLIERT = "/Applications/JPEGdrop.app/Contents/Resources/jpegdrop.py"


def _uebergebene_pfade() -> list[str]:
    """Die Pfade, nach denen install.sh selbst suchen laesst.

    Der Aufruf wird woertlich aus dem Skript geschnitten und mit einem
    Platzhalter statt der echten Funktion ausgefuehrt — geprueft wird also die
    tatsaechliche Argumentliste, nicht eine Kopie im Test.
    """
    quelle = INSTALL.read_text(encoding="utf-8")
    start = quelle.index("stop_repo_instances \\")
    ende = quelle.index("\n/usr/bin/swift", start)
    aufruf = quelle[start:ende]
    snippet = (
        "set -euo pipefail\n"
        'DESTINATION="/Applications/JPEGdrop.app"\n'
        'APP="JPEGdrop.app"\n'
        'stop_repo_instances() { printf "%s\\n" "$@"; }\n'
        f"{aufruf}\n"
    )
    proc = subprocess.run(["bash", "-c", snippet], capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.splitlines()


def test_laufende_konvertierung_wird_mitbeendet():
    """Die Menueleisten-App startet fuer jeden Drop die CLI als eigenen Prozess.

    Dessen Argumentliste nennt jpegdrop.py, nicht menu_app.py — wer nur nach
    menu_app.py sucht, tauscht das Bundle unter einer laufenden Konvertierung
    aus.
    """
    pfade = _uebergebene_pfade()
    cli_pfade = [p for p in pfade if p.endswith("/jpegdrop.py")]
    assert cli_pfade, f"install.sh sucht keine laufende Konvertierung: {pfade}"
    assert CLI_INSTALLIERT in cli_pfade, cli_pfade
    liste = (
        f"  801 python3 /Applications/JPEGdrop.app/Contents/Resources/python/bin/python3 "
        f"{CLI_INSTALLIERT} --json /Users/x/bild.heic"
    )
    assert _stop_instances(liste, pfade) == ["801"]


def test_installation_bricht_ab_wenn_ein_prozess_nicht_endet():
    """`kill` schickt nur SIGTERM und kehrt sofort zurueck.

    Endet der Prozess nicht innerhalb der Frist, muss der Lauf abbrechen —
    sonst wird das Bundle unter einer laufenden Konvertierung ausgetauscht.
    """
    liste = f"  901 python3 python3 {CLI_INSTALLIERT}"
    snippet = _stop_snippet(liste, [CLI_INSTALLIERT], bleibt_am_leben=True,
                            timeout_sekunden="1")
    proc = subprocess.run(["bash", "-c", snippet], capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode != 0, proc.stdout
    assert "901" in proc.stderr, proc.stderr
    assert "laufen nach" in proc.stderr, proc.stderr


def test_ohne_treffer_wird_nicht_gewartet():
    """Ohne laufende Instanz darf die Frist gar nicht erst anlaufen."""
    liste = "  999 python3 /usr/bin/python3 /ganz/woanders/app/menu_app.py"
    snippet = _stop_snippet(liste, [INSTALLIERT], bleibt_am_leben=True,
                            timeout_sekunden="60")
    proc = subprocess.run(["bash", "-c", snippet], capture_output=True,
                          text=True, timeout=20)
    assert proc.returncode == 0, proc.stderr


def _actual_stop_run(prozessliste: str, *, timeout: str,
                     bleibt_am_leben: bool) -> subprocess.CompletedProcess:
    """Den echten STOP_TIMEOUT-/Funktions-/Aufrufblock aus install.sh ausfuehren."""
    quelle = INSTALL.read_text(encoding="utf-8")
    start = quelle.index('STOP_TIMEOUT="${JPEGDROP_STOP_TIMEOUT:-10}"')
    ende = quelle.index("\n/usr/bin/swift", start)
    block = quelle[start:ende]
    lebt = "return 0" if bleibt_am_leben else "return 1"
    snippet = (
        "set -euo pipefail\n"
        'DESTINATION="/Applications/JPEGdrop.app"\nAPP="JPEGdrop.app"\n'
        f"ps() {{ if [ \"${{1:-}}\" = \"-p\" ]; then {lebt}; fi; "
        f"cat <<'LISTE'\n{prozessliste}\nLISTE\n}}\n"
        'kill() { if [ "$1" = "-0" ]; then ' + lebt + '; fi; return 0; }\n'
        'sleep() { :; }\n'
        f"{block}\n"
        'echo INSTALL_CONTINUES\n'
    )
    return subprocess.run(
        ["bash", "-c", snippet],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=20,
        env={**os.environ, "JPEGDROP_STOP_TIMEOUT": timeout},
    )


def test_actual_call_site_propagates_stop_failure():
    """Die echte Aufrufstelle darf den Fehler nicht mit ``|| true`` verschlucken."""
    cli = REPO / "jpegdrop.py"
    liste = f"  910 python3 python3 {cli} --json /tmp/bild.png"
    proc = _actual_stop_run(liste, timeout="1", bleibt_am_leben=True)
    assert proc.returncode != 0, proc.stdout
    assert "INSTALL_CONTINUES" not in proc.stdout
    assert "910" in proc.stderr, proc.stderr


def test_invalid_stop_timeout_is_normalized_before_any_signal():
    proc = _actual_stop_run("", timeout="ungueltig", bleibt_am_leben=False)
    assert proc.returncode == 0, proc.stderr
    assert "INSTALL_CONTINUES" in proc.stdout
    assert "verwende 10s" in proc.stderr, proc.stderr

    proc = _actual_stop_run("", timeout="0", bleibt_am_leben=False)
    assert proc.returncode == 0, proc.stderr
    assert "mindestens 1" in proc.stderr, proc.stderr


def _assess(spctl_rc: int) -> subprocess.CompletedProcess:
    """assess_gatekeeper aus install.sh mit einem spctl-Platzhalter aufrufen.

    Das Skript wird nicht ausgeführt: sed schneidet nur die Funktion heraus.
    """
    quelle = RELEASE_UTILS.read_text(encoding="utf-8")
    start = quelle.index("assess_gatekeeper() {")
    ende = quelle.index("\n}\n", start) + len("\n}\n")
    funktion = quelle[start:ende]
    snippet = (
        "set -euo pipefail\n"
        f'spctl() {{ echo "SPCTL-AUSGABE"; return {spctl_rc}; }}\n'
        f"{funktion}\n"
        'assess_gatekeeper /pfad/zur/JPEGdrop.app --type exec\n'
        'echo "INSTALL OK"\n'
    )
    return subprocess.run(["bash", "-c", snippet], capture_output=True,
                          text=True, timeout=60)


def test_gatekeeper_rejection_stops_the_run():
    proc = _assess(3)
    assert proc.returncode != 0, proc.stdout
    assert "INSTALL OK" not in proc.stdout, proc.stdout
    assert "Gatekeeper lehnt" in proc.stderr, proc.stderr


def test_gatekeeper_acceptance_continues():
    proc = _assess(0)
    assert proc.returncode == 0, proc.stderr
    assert "INSTALL OK" in proc.stdout, proc.stdout
    # Die spctl-Ausgabe soll weiterhin sichtbar sein.
    assert "SPCTL-AUSGABE" in proc.stdout, proc.stdout


if __name__ == "__main__":
    # Direkt ausführbar ohne pytest
    tests = [
        test_applications_is_blocked,
        test_subdirectory_of_applications_is_blocked,
        test_detour_via_parent_directory_is_blocked,
        test_detour_from_an_unprotected_prefix_is_blocked,
        test_system_is_blocked,
        test_symlink_to_applications_is_blocked,
        test_non_app_path_is_still_rejected,
        test_resolve_path_propagates_an_unreadable_parent,
        test_swap_replaces_a_broken_target_symlink,
        test_failed_swap_restores_the_previous_bundle,
        test_exit_trap_restores_bundle_and_cleans_all_temp_directories,
        test_bundle_path_scan_fails_closed_on_a_grep_error,
        test_launcher_wird_nicht_vom_bauskript_befuellt,
        test_interpreter_pruefsumme_ist_gepinnt,
        test_python_nebenversion_kommt_aus_pbs_python,
        test_bauskript_prueft_ganzes_bundle_auf_mac_pfad,
        test_bauskript_kompiliert_ohne_mac_pfad_vor,
        test_gebautes_bundle_ohne_mac_pfad,
        test_gebautes_bundle_ohne_symlink_nach_draussen,
        test_installierte_instanz_wird_beendet,
        test_entwicklungsinstanz_aus_dem_repo_wird_beendet,
        test_entwicklungsinstanz_unter_pfad_mit_leerzeichen_wird_beendet,
        test_fremde_prozesse_bleiben_verschont,
        test_fremde_python_werkzeuge_mit_skript_als_argument_bleiben_verschont,
        test_jede_instanz_nur_einmal_beendet,
        test_installationsskript_beendet_sich_nicht_selbst,
        test_nicht_beendbarer_prozess_bricht_vor_dem_austausch_ab,
        test_laufende_konvertierung_wird_mitbeendet,
        test_installation_bricht_ab_wenn_ein_prozess_nicht_endet,
        test_ohne_treffer_wird_nicht_gewartet,
        test_actual_call_site_propagates_stop_failure,
        test_invalid_stop_timeout_is_normalized_before_any_signal,
        test_gatekeeper_rejection_stops_the_run,
        test_gatekeeper_acceptance_continues,
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
