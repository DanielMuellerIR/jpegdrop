#!/usr/bin/env bash
# build.sh — richtet jpegdrop auf DIESEM Mac ein.
#
# 1) Self-contained venv anlegen (gitignored, pro Mac) — Werkzeug fürs Bauen
#    und für die Tests, landet NICHT im Bundle.
# 2) PyObjC-Cocoa-App bauen — reguläre App MIT Dock-Icon (Dock-Droplet),
#    zusätzlich "JD"-Kürzel in der Menüleiste (kein LSUIElement mehr seit v2).
# 3) Eigenen Python-Interpreter ins Bundle legen, damit die App vollständig
#    ist und keinen Pfad dieses Macs enthält.
# 4) Icon einbacken.
# 5) Alles Mitgelieferte einzeln signieren, dann notarisieren (graceful skip).
#
# Idempotent: einfach erneut ausführen nach einem git pull.
#
# Pfade beim Bau:
#   APP_FINAL = endgueltiger Ort des Bundles
#   WORKDIR   = Baustelle als Geschwister von APP_FINAL
#   APP       = Bundle in WORKDIR; nach dem Austausch identisch mit APP_FINAL
#   ALT       = beiseitegelegte Vorgaengerversion in einem eigenen Geschwisterordner
# Erst das vollstaendig gepruefte APP ersetzt APP_FINAL. Bis dieser Austausch
# belegt ist, stellt der EXIT-Trap eine beiseitegelegte Vorgaengerversion zurueck.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/release-utils.sh"
VENV="$HERE/.venv"
PY="$VENV/bin/python"
APP="${JPEGDROP_APP_PATH:-$HERE/JPEGdrop.app}"

# Das Bauziel kommt frei aus der Umgebung. Deshalb erst normalisieren, dann die
# geschützten Orte ausschließen: JPEGDROP_APP_PATH=/Applications/JPEGdrop.app
# würde dort sonst einen Build einsetzen, der die Notarisierung notfalls still
# überspringt (s. u.). Nach /Applications installiert allein ./install.sh, und
# nur mit angeheftetem Ticket.
#
# Normalisiert wird der längste bereits existierende Anteil des Pfades (per "cd"
# + "pwd -P", das löst Symlinks auf); was es noch nicht gibt, kann kein Symlink
# sein und wird nur angehängt. Angelegt wird dabei nichts — sonst stünde schon ein
# leeres Verzeichnis im gesperrten Ort, bevor wir ablehnen.
#
# Im noch nicht vorhandenen Rest sind "." und ".." verboten: Sie bleiben hier
# stehen, waehrend "mkdir -p" sie spaeter sehr wohl aufloest. Ein Ziel wie
# /tmp/neu/../../../Applications/JPEGdrop.app haette die Sperre unten also
# passiert und trotzdem in /Applications gebaut.
resolve_path() {
  local pfad="$1" rest="" teil
  case "$pfad" in /*) ;; *) pfad="$PWD/$pfad" ;; esac
  while [ ! -d "$pfad" ] && [ "$pfad" != "/" ]; do
    teil="$(basename "$pfad")"
    case "$teil" in
      .|..)
        echo "Fehler: '.' oder '..' in einem noch nicht vorhandenen Teil von '$1'." >&2
        echo "Das Bauziel bitte ohne solche Umwege angeben." >&2
        return 2
        ;;
    esac
    rest="$teil${rest:+/$rest}"
    pfad="$(dirname "$pfad")"
  done
  pfad="$(cd "$pfad" && pwd -P)" || return 2
  if [ -n "$rest" ]; then
    printf '%s/%s\n' "${pfad%/}" "$rest"
  else
    printf '%s\n' "$pfad"
  fi
}

validate_app_path() {
  local candidate="$1" applications="${2:-/Applications}" system="${3:-/System}"
  case "$candidate" in
    *.app) ;;
    *) echo "Fehler: JPEGDROP_APP_PATH muss auf ein .app-Bundle zeigen." >&2; return 2 ;;
  esac
  candidate="$(resolve_path "$candidate")" || return 2
  case "$candidate" in
    "$applications"|"$applications"/*|"$system"|"$system"/*)
      echo "Fehler: $candidate ist als Bauziel gesperrt." >&2
      echo "Dorthin installiert ausschließlich ./install.sh (notarisiert)." >&2
      return 2
      ;;
  esac
  printf '%s\n' "$candidate"
}

APP="$(validate_app_path "$APP")" || exit 2

# Gebaut wird NEBEN dem Ziel, nicht darin: Der Bau dauert Minuten (Download,
# pip, Vorkompilieren, Signieren, Notarisieren). Wurde vorher das vorhandene
# Bundle geloescht, war in dieser Zeit keine App da — ein Fehler mittendrin
# liess gar keine zurueck, und ein Drop auf eine gerade laufende Instanz traf
# einen halb aufgebauten Interpreter. Erst das fertig gepruefte Bundle nimmt am
# Ende den endgueltigen Platz ein.
#
# Das Arbeitsverzeichnis liegt als Geschwister des Ziels (also im selben
# Dateisystem), damit der Austausch ein reines Umbenennen ist. Das Bundle darin
# heisst wie das endgueltige — der Name wandert in die Notarisierung.
APP_FINAL="$APP"
WORKDIR="$(dirname "$APP_FINAL")/.jpegdrop-build.$$"
APP="$WORKDIR/$(basename "$APP_FINAL")"
ALT=""
ALT_DIR=""
SWAP_DONE=0
ENTITLEMENTS=""
ENTITLEMENTS_DIR=""
PBS_TMP=""
ICON_TMP=""
NZIP_DIR=""
aufraeumen() {
  # Bewusst mit "if" statt "&&": Unter `set -e` wuerde ein leerer Test die
  # Aufraeumfunktion sonst mittendrin abbrechen.
  if [ -n "$ALT" ] && { [ -e "$ALT" ] || [ -L "$ALT" ]; }; then
    if [ "$SWAP_DONE" -eq 1 ]; then
      rm -rf "$ALT"
    elif [ ! -e "$APP_FINAL" ] && [ ! -L "$APP_FINAL" ]; then
      if mv "$ALT" "$APP_FINAL"; then
        echo "    Bisheriges Bundle nach Abbruch wiederhergestellt." >&2
        ALT=""
      else
        echo "FEHLER: Bisheriges Bundle konnte nicht wiederhergestellt werden." >&2
        echo "  Es bleibt erhalten unter: $ALT" >&2
      fi
    else
      echo "HINWEIS: Bisheriges Bundle bleibt erhalten unter: $ALT" >&2
    fi
  fi
  if [ -n "$ALT_DIR" ] && [ ! -e "$ALT" ] && [ ! -L "$ALT" ]; then
    rm -rf "$ALT_DIR"
  fi
  if [ -n "$WORKDIR" ]; then
    rm -rf "$WORKDIR"
  fi
  if [ -n "$ENTITLEMENTS_DIR" ]; then
    rm -rf "$ENTITLEMENTS_DIR"
  fi
  if [ -n "$PBS_TMP" ]; then
    rm -rf "$PBS_TMP"
  fi
  if [ -n "$ICON_TMP" ]; then
    rm -rf "$ICON_TMP"
  fi
  if [ -n "$NZIP_DIR" ]; then
    rm -rf "$NZIP_DIR"
  fi
  return 0
}
trap aufraeumen EXIT
# Bash fuehrt den EXIT-Trap bei einem direkt empfangenen Signal nicht auf allen
# 3.2-Pfaden verlaesslich aus. Die Signal-Traps wandeln Abbrueche deshalb in
# normale Exits um; deren Status bleibt shellueblich 130 (INT) bzw. 143 (TERM).
trap 'exit 130' INT
trap 'exit 143' TERM

APP_RES="$APP/Contents/Resources"
APP_MACOS="$APP/Contents/MacOS"
ICON_MASTER="$HERE/assets/icon-1024.png"
CODESIGN_BIN="${CODESIGN_BIN:-codesign}"

# --- Eingebetteter Python-Interpreter -----------------------------------------
# Das Bundle bringt seinen eigenen Interpreter mit, damit es keinen Pfad dieses
# Macs enthält. Quelle ist python-build-standalone (Projekt von astral-sh): ein
# fertig gebautes CPython, das sich an einen beliebigen Ort entpacken lässt.
# Die Variante "install_only_stripped" ist die kleine Auslieferungsfassung ohne
# Debug-Symbole; sie enthält keinen einzigen Symlink nach draußen und alle
# Erweiterungsmodule der Standardbibliothek fest im Interpreter selbst.
#
# Version und Prüfsumme stehen hier fest, damit jeder Mac dasselbe baut. Die
# Prüfsumme stammt aus der Datei SHA256SUMS desselben Releases und wurde am
# 2026-08-04 gegen den heruntergeladenen Stand verglichen. Neue Fassung: beide
# Zeilen zusammen austauschen, nie nur eine.
PBS_RELEASE="20260728"
PBS_PYTHON="3.14.6"
# Nur Haupt- und Nebenversion ("3.14.6" -> "3.14"). So heißen im Archiv die
# Ordner lib/python3.14 und das Programm bin/python3.14. Unten steht diese
# Nummer deshalb nirgends fest im Text: Beim Wechsel auf eine neue Python-Fassung
# liefe das Ausdünnen sonst ins Leere, weil `rm -rf` fehlende Pfade still
# übergeht — die App würde unbemerkt rund 45 MB größer.
PBS_PYMINOR="${PBS_PYTHON%.*}"
PBS_ARCHIVE="cpython-$PBS_PYTHON+$PBS_RELEASE-aarch64-apple-darwin-install_only_stripped.tar.gz"
PBS_SHA256="f4b47659e2da4b97f38cefdf5ad19f0042946099d843cde60de308708e5b1ac5"
CACHE="$HERE/.cache"
echo "==> venv + Abhängigkeiten (Pillow, pi-heif, PyObjC-Cocoa)"
if [ ! -x "$PY" ]; then
  python3 -m venv "$VENV"
fi
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -r "$HERE/requirements-app.txt"

# $HERE als Argument uebergeben, nicht in den Python-Quelltext einsetzen: ein
# Apostroph im Repo-Pfad wuerde das Stringliteral sonst beenden (SyntaxError,
# im schlimmsten Fall eingeschleuster Python-Code).
VERSION="$("$PY" -c 'import sys; sys.path.insert(0, sys.argv[1]); import jpegdrop; print(jpegdrop.__version__)' "$HERE")"
echo "==> Version: $VERSION"

echo "==> App-Bundle-Struktur aufbauen"
# "$APP" liegt im frischen Arbeitsverzeichnis (s. o.); das vorhandene Bundle am
# endgueltigen Ort bleibt bis zum Austausch am Ende unangetastet.
rm -rf "$WORKDIR"
mkdir -p "$APP_RES" "$APP_MACOS"

# Info.plist mit eingesetzter Version
sed -e "s|__VERSION__|$VERSION|g" \
    "$HERE/app/Info.plist" > "$APP/Contents/Info.plist"

# Die Python-Module ins Bundle. Sie werden dort wirklich ausgeführt (der
# Launcher oben zeigt darauf), sind also keine Diagnosekopien mehr.
mkdir -p "$APP_RES/app"
cp "$HERE/jpegdrop.py" "$APP_RES/jpegdrop.py"
cp "$HERE/app/"*.py "$APP_RES/app/"
cp "$HERE/assets/privacy.html" "$APP_RES/privacy.html"

sparkle_args=(--app "$APP" --cache "$HERE/.cache/sparkle-2.10.0")
if [ -n "${JPEGDROP_TEST_FEED_URL:-}" ]; then
  sparkle_args+=(--test-feed-url "$JPEGDROP_TEST_FEED_URL")
fi
"$PY" "$HERE/tools/sparkle.py" "${sparkle_args[@]}"
"$PY" "$HERE/tools/build_manifest.py" --write --repo "$HERE" --app "$APP" \
  --python-version "$PBS_PYTHON" --python-release "$PBS_RELEASE"

# Optionale Metadata-Datei (manche macOS-Tools wollen PkgInfo)
printf 'APPL????' > "$APP/Contents/PkgInfo"

echo "==> Eigenen Python-Interpreter ins Bundle legen ($PBS_PYTHON)"
mkdir -p "$CACHE"
PBS_TARBALL="$CACHE/$PBS_ARCHIVE"

# Prüfsumme immer prüfen, auch bei einer schon vorhandenen Cache-Datei: ein
# abgebrochener Download hinterlässt sonst eine halbe Datei, die bei jedem
# weiteren Lauf wiederverwendet würde.
pruefsumme_stimmt() {
  [ -f "$PBS_TARBALL" ] || return 1
  local ist
  ist="$(shasum -a 256 "$PBS_TARBALL" | cut -d' ' -f1)"
  [ "$ist" = "$PBS_SHA256" ]
}

if pruefsumme_stimmt; then
  echo "    aus dem Cache: $PBS_ARCHIVE"
else
  rm -f "$PBS_TARBALL"
  # Im Dateinamen steht ein Pluszeichen; in einer URL muss es als %2B stehen,
  # sonst liefert GitHub 404.
  PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/$PBS_RELEASE/${PBS_ARCHIVE//+/%2B}"
  echo "    lade herunter (rund 26 MB) …"
  if ! curl -fL --max-time 600 -o "$PBS_TARBALL.teil" "$PBS_URL"; then
    rm -f "$PBS_TARBALL.teil"
    echo "Fehler: Interpreter-Archiv nicht ladbar ($PBS_URL)." >&2
    echo "Ohne Netz geht es nur mit gefülltem Cache: $CACHE" >&2
    exit 1
  fi
  mv "$PBS_TARBALL.teil" "$PBS_TARBALL"
  if ! pruefsumme_stimmt; then
    # Fail-closed: lieber gar kein Bundle als eines mit einem Interpreter
    # unbekannter Herkunft. Die Datei bleibt zur Ansicht liegen.
    echo "Fehler: Prüfsumme des Interpreter-Archivs passt nicht." >&2
    echo "  erwartet: $PBS_SHA256" >&2
    echo "  bekommen: $(shasum -a 256 "$PBS_TARBALL" | cut -d' ' -f1)" >&2
    exit 1
  fi
fi

# Entpacken in ein eigenes Verzeichnis und erst dann ins Bundle schieben — so
# steht im Bundle entweder ein vollständiger Interpreter oder gar keiner.
PBS_TMP="$(mktemp -d)"
tar -xzf "$PBS_TARBALL" -C "$PBS_TMP"
rm -rf "$APP_RES/python"
mv "$PBS_TMP/python" "$APP_RES/python"
rm -rf "$PBS_TMP"
PBS_TMP=""

# Fail-closed: Passen die Ordnernamen im Archiv nicht zu PBS_PYMINOR, würden
# Ausdünnen und Signatur-Sonderregel unten ihre Pfade nicht finden, ohne dass der
# Bau scheitert. Dann lieber hier abbrechen.
BUNDLE_PYLIB="$APP_RES/python/lib/python$PBS_PYMINOR"
if [ ! -d "$BUNDLE_PYLIB/site-packages" ] || [ ! -x "$APP_RES/python/bin/python$PBS_PYMINOR" ]; then
  echo "Fehler: Interpreter-Archiv passt nicht zu PBS_PYTHON=$PBS_PYTHON" >&2
  echo "  (erwartet lib/python$PBS_PYMINOR/site-packages und bin/python$PBS_PYMINOR)." >&2
  exit 1
fi

BUNDLE_PY="$APP_RES/python/bin/python3"
echo "    Abhängigkeiten in den eingebetteten Interpreter installieren"
"$BUNDLE_PY" -m pip install --quiet --disable-pip-version-check --no-cache-dir -r "$HERE/requirements-app.txt"
PYTHONDONTWRITEBYTECODE=1 "$BUNDLE_PY" "$HERE/tools/collect_licenses.py" --repo "$HERE" --app "$APP"

# Der native Prozess bleibt im äußeren App-Bundle. Ein exec des Python-
# Programms änderte NSBundle.mainBundle; Sparkle behandelte JPEGdrop dann
# als externes Update-Ziel und ließ die alte App ohne Neustart weiterlaufen.
xcrun clang -arch arm64 -mmacosx-version-min=12.0 -O2 -g0 "$HERE/app/launcher.m" \
  -I "$APP_RES/python/include/python$PBS_PYMINOR" -L "$APP_RES/python/lib" \
  "-lpython$PBS_PYMINOR" -framework Foundation \
  -Wl,-rpath,@executable_path/../Resources/python/lib -o "$APP_MACOS/JPEGdrop"


# Ausdünnen. Zwei Gründe: Die App wird sonst rund 45 MB größer als nötig, und
# jede mitgelieferte Programmdatei müsste unten einzeln signiert werden. Allein
# PyObjCTest bringt 140 solcher Dateien mit, die im Betrieb nie geladen werden.
# Erst NACH dem pip-Lauf, denn pip selbst fliegt hier mit raus.
echo "    nicht benötigte Teile entfernen"
BSP="$BUNDLE_PYLIB/site-packages"
rm -rf \
  "$APP_RES/python/include" "$APP_RES/python/share" \
  "$APP_RES/python"/lib/tcl* "$APP_RES/python"/lib/tk* \
  "$APP_RES/python"/lib/itcl* "$APP_RES/python"/lib/thread* \
  "$APP_RES/python"/lib/libtcl* "$APP_RES/python"/lib/libtk* \
  "$APP_RES/python/lib/pkgconfig" \
  "$BUNDLE_PYLIB/idlelib" \
  "$BUNDLE_PYLIB/tkinter" \
  "$BUNDLE_PYLIB/ensurepip" \
  "$BUNDLE_PYLIB/pydoc_data" \
  "$BUNDLE_PYLIB"/lib-dynload/_tkinter*.so \
  "$BSP/PyObjCTest" "$BSP/pip" "$BSP"/pip-*.dist-info \
  "$BSP/setuptools" "$BSP"/setuptools-*.dist-info \
  "$BSP/pkg_resources" "$BSP/_distutils_hack" "$BSP/distutils-precedence.pth" \
  "$APP_RES/python"/bin/idle* "$APP_RES/python"/bin/pydoc* \
  "$APP_RES/python"/bin/pip* "$APP_RES/python"/bin/*-config
# libpython bleibt für den nativen GUI-Starter erhalten. Der eigenständige
# Interpreter unter bin/python3 wird weiterhin für CLI-Aufträge verwendet.
find "$APP_RES/python" -name '__pycache__' -type d -prune -exec rm -rf {} +

# Sofort nachsehen, ob der eingebettete Interpreter alles laden kann. Ein Fehler
# hier ist ein Ausdünn-Fehler und soll den Bau abbrechen, statt erst beim ersten
# Bilddrop aufzufallen.
#
# PYTHONDONTWRITEBYTECODE, damit dieser Lauf keine .pyc-Dateien hinterlässt: Sie
# würden den Pfad DIESES Macs einbacken (Python schreibt den Quellpfad in jede
# .pyc). Erzeugt werden sie gleich darunter, dann mit gekürzten Pfaden.
PYTHONDONTWRITEBYTECODE=1 "$BUNDLE_PY" -c 'import PIL, pi_heif, objc, AppKit, Foundation' \
  || { echo "Fehler: eingebetteter Interpreter unvollständig." >&2; exit 1; }
"$PY" "$HERE/tools/check_compatibility.py" --app "$APP"

# Vorkompilieren. Zwei Gründe:
#
# 1. Tempo. Ohne .pyc übersetzt Python die Module bei JEDEM Start neu — das kostet
#    rund 0,3 s, und zwar nicht nur beim Öffnen der App, sondern bei jedem Drop
#    (die Konvertierung läuft als eigener Prozess). Mit .pyc sind es 0,1 s. Preis
#    sind rund 15 MB.
# 2. Unversehrtheit der Signatur. Fehlt eine .pyc, schreibt Python sie zur
#    Laufzeit ins Bundle — in einem signierten Bundle macht schon eine neue Datei
#    die Signatur ungültig. Liegen sie fertig da, entsteht kein Schreibbedarf.
#
# -s/-p ersetzen den Pfad, den Python in jede .pyc schreibt: statt dem Ort auf
# diesem Mac steht dort "/JPEGdrop.app/…". "unchecked-hash" heißt: Python nimmt
# die .pyc, ohne die Quelldatei zu vergleichen — auch das verhindert Schreiben.
echo "    Module vorkompilieren"
"$BUNDLE_PY" -m compileall -q -f --invalidation-mode unchecked-hash \
  -s "$APP" -p "/JPEGdrop.app" "$APP_RES" >/dev/null

# Icon einbacken (falls Master vorhanden)
if [ -f "$ICON_MASTER" ]; then
  echo "==> Icon einbacken"
  ICON_TMP="$(mktemp -d)"
  ICONSET="$ICON_TMP/jpegdrop.iconset"
  mkdir -p "$ICONSET"
  "$PY" - "$ICON_MASTER" "$ICONSET" <<'PY'
import sys
from PIL import Image
master, iconset = sys.argv[1], sys.argv[2]
src = Image.open(master).convert("RGBA")
for base in (16, 32, 128, 256, 512):
    for scale in (1, 2):
        px = base * scale
        name = f"icon_{base}x{base}{'@2x' if scale == 2 else ''}.png"
        src.resize((px, px), Image.LANCZOS).save(f"{iconset}/{name}")
PY
  ICONNAME="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIconFile' "$APP/Contents/Info.plist" 2>/dev/null || echo applet)"
  ICONNAME="${ICONNAME%.icns}"
  iconutil -c icns "$ICONSET" -o "$APP_RES/$ICONNAME.icns"
  rm -rf "$ICON_TMP"
  ICON_TMP=""
  rm -f "$APP_RES/Assets.car"
  touch "$APP"
fi

# Hinweis: Der frühere "droplet -> JPEGdrop"-Umbenenn-Schritt (für sprechende
# Namen in den macOS-Datenschutz-Listen) entfällt ab v2.0.0 — das Bundle wird hier
# bereits direkt mit Executable "JPEGdrop" + passendem CFBundleExecutable gebaut
# (s. o.), es gibt keinen generischen "droplet" mehr.

# --- Signieren ----------------------------------------------------------------
# Seit das Bundle einen eigenen Interpreter mitbringt, reicht es nicht mehr, nur
# das Bundle zu signieren: Jede mitgelieferte Programmdatei braucht eine eigene
# Signatur, sonst lehnt die Notarisierung sie ab. Signiert wird von innen nach
# außen — erst die einzelnen Dateien, zuletzt das Bundle, das sie umschließt.

mach_o_dateien() {
  # Nur Bibliotheken (.so/.dylib) und ausführbare Dateien näher ansehen; `file`
  # für jede der rund 2000 Bundle-Dateien aufzurufen wäre unnötig langsam.
  local f
  while IFS= read -r f; do
    case "$(file -b "$f")" in Mach-O*) printf '%s\n' "$f" ;; esac
  done < <(find "$APP" -type f \( -name '*.so' -o -name '*.dylib' -o -perm +111 \))
}

# Bei Hardened Runtime prüft macOS beim Laden jeder Bibliothek, ob sie vom
# selben Entwickler stammt wie der laufende Prozess. Mit Developer ID stimmt das
# überall, weil unten alles mit derselben Kennung signiert wird. Ad-hoc-
# Signaturen haben dagegen gar keinen Entwickler — dann lehnt macOS beim Start
# schon Pillow ab ("different Team IDs"). Für diesen Fall erlaubt die
# Berechtigung unten dem Interpreter ausdrücklich, fremd signierte Bibliotheken
# zu laden. Am 2026-08-04 mit beiden Wegen nachgestellt und geprüft.
ENTITLEMENTS_DIR="$(mktemp -d -t jpegdrop-ent)"
ENTITLEMENTS="$ENTITLEMENTS_DIR/entitlements.plist"
cat > "$ENTITLEMENTS" <<'ENT'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>com.apple.security.cs.disable-library-validation</key><true/>
</dict></plist>
ENT
# Aufgeraeumt wird zentral in `aufraeumen` (oben als EXIT-Trap gesetzt): Ein
# zweiter EXIT-Trap wuerde den ersten ersetzen, und das halbfertige
# Arbeitsverzeichnis bliebe nach einem Abbruch liegen.

alles_signieren() {
  # $1 = Signatur-Kennung, "-" bedeutet ad-hoc.
  local id="$1" f anzahl=0 meldung
  local -a opts=(--force --options runtime)
  # Zeitstempel vom Apple-Server: für die Notarisierung Pflicht, bei ad-hoc
  # nicht möglich.
  [ "$id" != "-" ] && opts+=(--timestamp)
  xattr -cr "$APP"
  # codesign meldet für jede Datei "replacing existing signature" — bei über
  # 40 Dateien vernebelt das die Ausgabe. Deshalb wird sie eingesammelt und nur
  # gezeigt, wenn wirklich etwas schiefgeht.
  signiere_eine() {
    if ! meldung="$("$CODESIGN_BIN" "$@" 2>&1)"; then
      echo "Fehler beim Signieren von ${*: -1}:" >&2
      echo "$meldung" >&2
      return 1
    fi
  }
  while IFS= read -r f; do
    if [ "$id" = "-" ] && { [ "$f" = "$APP_RES/python/bin/python$PBS_PYMINOR" ] || [ "$f" = "$APP_MACOS/JPEGdrop" ]; }; then
      signiere_eine "${opts[@]}" --entitlements "$ENTITLEMENTS" --sign "$id" "$f" || return 1
    else
      signiere_eine "${opts[@]}" --sign "$id" "$f" || return 1
    fi
    anzahl=$((anzahl + 1))
  done < <(mach_o_dateien)
  # Die inneren Programme sind bereits signiert. Jetzt deren Container und
  # das Framework versiegeln, bevor das äußere App-Bundle signiert wird.
  local framework="$APP/Contents/Frameworks/Sparkle.framework"
  signiere_eine "${opts[@]}" --sign "$id" "$framework/Versions/B/Updater.app" || return 1
  signiere_eine "${opts[@]}" --sign "$id" "$framework" || return 1
  if [ "$id" = "-" ]; then
    signiere_eine "${opts[@]}" --entitlements "$ENTITLEMENTS" --sign "$id" "$APP" || return 1
  else
    signiere_eine "${opts[@]}" --sign "$id" "$APP" || return 1
  fi
  echo "    $anzahl mitgelieferte Programmdateien + Bundle signiert"
}

# --- Developer-ID-Signatur + Notarisierung (auto, graceful) ------------------
# Ad-hoc reicht zum lokalen Start; für verteilbare/warnungsfreie Apps zusätzlich
# Developer-ID (Hardened Runtime) + notarisieren + Ticket stapeln. Wird STILL
# übersprungen (Build läuft durch), wenn: kein Developer-ID-Cert, Profil
# das Notary-Profil nicht nutzbar (Keychain gesperrt, z.B. über SSH) oder NO_NOTARIZE=1.
set +e
SIGN_ID="$(resolve_signing_identity "$HERE")"
SIGN_STATUS=$?
if [ "$SIGN_STATUS" -gt 1 ]; then
  exit "$SIGN_STATUS"
fi
NOTARY_PROFILE="$(resolve_notary_profile "$HERE")"
# Fünf Versuche statt einem: `notarytool history` meldet gelegentlich fälschlich
# „No Keychain password item found", obwohl das Profil da ist (bei einem früheren Lauf
# belegt — Versuch 1 fehlgeschlagen, Versuch 2 sofort ok). Hier wiegt das
# besonders schwer: Ein Fehlversuch überspringt die Notarisierung STILL, die App
# landet also ohne Ticket in /Applications, ohne dass der Lauf scheitert.
if [ -z "${NO_NOTARIZE:-}" ] && [ -n "$SIGN_ID" ] && notary_profile_works "$NOTARY_PROFILE"; then
  echo "==> Developer-ID signieren + notarisieren ($NOTARY_PROFILE)…"
  # Scheitert schon das Signieren, hat das Einreichen keinen Sinn — dann lieber
  # hier abbrechen, statt die Notarisierung an derselben Ursache scheitern zu
  # lassen und das hinterher als "nicht akzeptiert" zu melden.
  if ! alles_signieren "$SIGN_ID"; then
    echo "Fehler: Developer-ID-Signatur fehlgeschlagen." >&2
    exit 1
  fi
  NZIP_DIR="$(mktemp -d)"
  NZIP="$NZIP_DIR/app.zip"
  ditto -c -k --keepParent "$APP" "$NZIP"
  if xcrun notarytool submit "$NZIP" --keychain-profile "$NOTARY_PROFILE" --wait; then
    xcrun stapler staple "$APP" && echo "   notarisiert + gestapelt ✓"
  else
    echo "   ⚠ Notarisierung nicht akzeptiert — App ist Developer-ID-signiert (lokal ok)."
  fi
  rm -rf "$NZIP_DIR"
  NZIP_DIR=""
else
  echo "==> Notarisierung übersprungen (kein Cert / Profil nicht nutzbar / NO_NOTARIZE). Lokale Signatur folgt."
  echo "    Über SSH gebaut? Login-Keychain ist dann gesperrt → 'security unlock-keychain ~/Library/Keychains/login.keychain-db' (fragt PW interaktiv) und neu bauen. Am Mac selbst (GUI) ist er offen."
fi

# Der optionale Notarisierungsblock darf fehlschlagen; die abschließende lokale
# Signatur-Garantie dagegen nicht. Ab hier Fehler wieder strikt propagieren.
set -e

# Signatur-Garantie: Lief oben Developer-ID/Notarisierung, ist alles schon
# gueltig (verify ok -> nichts tun). Sonst (kein Cert / NO_NOTARIZE) ad-hoc
# signieren — auf Apple Silicon Pflicht, sonst startet die App nicht.
# --options runtime (Hardened Runtime) ist auf Apple Silicon Pflicht; ohne es
# verweigert Gatekeeper den Start selbst bei einer gültigen Ad-hoc-Signatur.
#
# Geprueft wird mit --deep: Ohne das sieht codesign nur das Bundle an und meldet
# "gueltig", auch wenn im mitgelieferten Interpreter eine unsignierte Datei
# liegt — genau die, an der die Notarisierung spaeter scheitern wuerde.
if ! "$CODESIGN_BIN" --verify --deep --strict "$APP" 2>/dev/null; then
  alles_signieren -
fi
"$CODESIGN_BIN" --verify --deep --strict "$APP"

# Letzte Bremse gegen einen Rueckfall: Das ausgelieferte Bundle darf NIRGENDS den
# Pfad dieses Macs enthalten — auch nicht in einer Binaerdatei.
#
# Zwei Details, die hier am 2026-08-04 wirklich schiefgingen:
#   1. Eine frueher engere Fassung sah nur den Launcher und die beiden
#      Python-Dateien an. Die .pyc-Dateien des Interpreters trugen den Bau-Pfad
#      und fielen durch. Deshalb wird jetzt das ganze Bundle geprueft.
#   2. ein als `grep` installiertes ugrep und ueberspringt Binaerdateien
#      stillschweigend — die Pruefung meldete "sauber", obwohl der Pfad drinstand.
#      Deshalb fest /usr/bin/grep mit -a (Binaerdateien wie Text behandeln).
grep_rc=0
fundstellen="$(/usr/bin/grep -raF -l "$HERE" "$APP" 2>"$WORKDIR/pfadpruefung.err")" || grep_rc=$?
if [ "$grep_rc" -ge 2 ]; then
  echo "Fehler: Das Bundle konnte nicht vollstaendig auf Mac-Pfade geprueft werden." >&2
  sed -n '1,5p' "$WORKDIR/pfadpruefung.err" >&2
  exit 1
fi
if [ -n "$fundstellen" ]; then
  echo "Fehler: Das Bundle enthaelt den Pfad dieses Macs ($HERE):" >&2
  printf '%s\n' "$fundstellen" | head -5 >&2
  exit 1
fi

# --- Fertiges Bundle an seinen endgueltigen Platz -----------------------------
# Alles geprueft: signiert, ggf. notarisiert, ohne Pfad dieses Macs. Erst jetzt
# wird das bisherige Bundle ersetzt, und zwar durch zwei Umbenennungen im selben
# Dateisystem (kein minutenlanges Fenster ohne App). Die Altfassung wandert in
# einen eigenen Geschwisterordner AUSSERHALB von WORKDIR: Scheitert das
# Einsetzen oder trifft ein Signal zwischen den Umbenennungen ein, stellt der
# EXIT-Trap sie zurueck und loescht sie keinesfalls mit der Baustelle.
if [ -e "$APP_FINAL" ] || [ -L "$APP_FINAL" ]; then
  ALT_DIR="$(mktemp -d "$(dirname "$APP_FINAL")/.jpegdrop-alt.XXXXXX")"
  ALT="$ALT_DIR/$(basename "$APP_FINAL")"
  mv "$APP_FINAL" "$ALT"
fi
if ! mv "$APP" "$APP_FINAL"; then
  echo "Fehler: Fertiges Bundle liess sich nicht nach '$APP_FINAL' einsetzen." >&2
  if [ -n "$ALT" ] && { [ -e "$ALT" ] || [ -L "$ALT" ]; } \
      && [ ! -e "$APP_FINAL" ] && [ ! -L "$APP_FINAL" ]; then
    if mv "$ALT" "$APP_FINAL"; then
      echo "    Bisheriges Bundle wiederhergestellt." >&2
      ALT=""
      rm -rf "$ALT_DIR"
      ALT_DIR=""
    else
      echo "FEHLER: Bisheriges Bundle konnte nicht wiederhergestellt werden." >&2
      echo "  Es bleibt erhalten unter: $ALT" >&2
    fi
  fi
  exit 1
fi
SWAP_DONE=1
APP="$APP_FINAL"
APP_RES="$APP/Contents/Resources"
if [ -n "$ALT" ]; then
  rm -rf "$ALT"
  ALT=""
fi
if [ -n "$ALT_DIR" ]; then
  rm -rf "$ALT_DIR"
  ALT_DIR=""
fi
rm -rf "$WORKDIR"
WORKDIR=""

echo "==> Fertig"
echo "    App:  $APP  ($(du -sh "$APP" | cut -f1))"
echo "    ->  Starten: open $APP"
echo "    Dann: 'JD' erscheint in der Menüleiste, dort Codec/Preset wählen."
echo "    Bilder droppen = aufs Finder-App-Icon oder (falls im Dock) aufs Dock-Icon."
echo
echo "    Headless / Agent (aus dem Repo):"
echo "    $PY $HERE/jpegdrop.py --list-presets"
echo "    $PY $HERE/jpegdrop.py --codec avif --preset mittel bild.heic"
echo "    …oder aus dem fertigen Bundle heraus, ganz ohne Repo:"
echo "    $APP_RES/python/bin/python3 $APP_RES/jpegdrop.py --list-presets"
