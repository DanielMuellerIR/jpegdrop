#!/usr/bin/env bash
# release.sh — JPEGdrop als notarisiertes DMG packen. Installiert NIE.
#
# Die drei Einstiegspunkte des Projekts trennen bewusst:
#   ./build.sh     baut die App im Projektverzeichnis; notarisiert, wenn es
#                  geht, überspringt es sonst still (Testbau soll durchlaufen)
#   ./install.sh   baut und installiert nach /Applications — nur mit Ticket
#   ./release.sh   baut, prüft das Ticket und packt das DMG — installiert nie
#
# Reihenfolge: Erst bekommt die APP ihr Notary-Ticket (das macht build.sh),
# dann das DMG. Ein Ticket allein am Image reicht nicht: Wer die App aus dem
# Image herauszieht, hätte sonst ein Bundle ohne eigenes. hdiutil kopiert das
# angeheftete Ticket als Datei im Bundle mit.
#
# Alles Zwischenzeitliche (Payload, schreibbares Image, Mountpunkt, fertiges
# aber ungeprüftes DMG) liegt in einem eigenen Arbeitsordner dieses Laufs.
# Erst das vollständig geprüfte Paar aus DMG und Prüfsumme wandert nach dist/,
# und zwar ohne Überschreiben: Liegt dort schon ein Artefakt dieser Version,
# bricht das Skript ab.
#
# Voraussetzungen:
#   - "Developer ID Application"-Zertifikat im Schlüsselbund
#   - notarytool-Profil, Name aus NOTARY_PROFILE oder `git config jpegdrop.notaryProfile`
#     (Default: jpegdrop-notary, wie in build.sh und install.sh)
#   - eine lokale Terminalsitzung: über SSH ist der Login-Schlüsselbund gesperrt
#
# Aufruf:
#   ./release.sh                     # vollständiger Lauf
#   ./release.sh --no-finder-layout  # ohne Finder-Fensterlayout (headless)
#
# Das Finder-Layout wird ohne GUI-Fokus direkt in .DS_Store geschrieben.
# --no-finder-layout überspringt diesen Schritt für ein schlichtes Testimage.
#
# Letzte Zeile bei Erfolg: RELEASE OK: <pfad-zum-dmg> (<version>)
set -euo pipefail

print_help() {
  awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$1"
}

FINDER_LAYOUT=1
for arg in "$@"; do
  case "$arg" in
    --no-finder-layout) FINDER_LAYOUT=0 ;;
    -h|--help) print_help "$0"; exit 0 ;;
    *) echo "Unbekannte Option: $arg" >&2
       echo "Aufruf: ./release.sh [--no-finder-layout]" >&2; exit 2 ;;
  esac
done

cd "$(dirname "$0")"
REPO="$PWD"
source "$REPO/release-utils.sh"
if [ -n "${JPEGDROP_TEST_FEED_URL:-}" ]; then
  echo "FEHLER: Ein Release darf keinen lokalen Update-Testfeed verwenden." >&2
  exit 2
fi
APP="$REPO/JPEGdrop.app"
DIST="$REPO/dist"
VOLNAME="JPEGdrop"

# build.sh baut nach JPEGDROP_APP_PATH. Fest auf das Bundle in DIESEM Repo
# setzen — dieselbe Absicherung wie in install.sh: Eine von außen gesetzte
# Variable ließe build.sh sonst woanders bauen, während unten ein altes
# JPEGdrop.app geprüft und verpackt würde.
export JPEGDROP_APP_PATH="$APP"

# Profilnamen bestimmen: Umgebung schlägt clone-lokale Git-Konfiguration.
NOTARY_PROFILE="$(resolve_notary_profile "$REPO")"
export NOTARY_PROFILE
SOURCE_COMMIT="$(/usr/bin/python3 "$REPO/tools/build_manifest.py" --check-source --repo "$REPO")"

# Profil- und Gatekeeper-Prüfung kommen aus release-utils.sh, damit Installation
# und Release dieselben Wiederholungen und dieselbe Statusauswertung verwenden.
# Aushängen mit Wiederholung: Nach Dateisystemzugriffen hält das System
# das Volume manchmal noch einen Moment fest. Bewusst ohne `-force` im ersten
# Anlauf — ein erzwungenes Aushängen kann laufende Schreibvorgänge abschneiden.
detach_own_device() {
  local device="$1" attempt
  for attempt in 1 2 3; do
    hdiutil detach "$device" >/dev/null 2>&1 && return 0
    sleep 2
  done
  hdiutil detach "$device" -force >/dev/null 2>&1
}

# Aufräumen darf ausschließlich anfassen, was DIESER Lauf angelegt hat: das von
# hdiutil gemeldete Device (nie ein Pfad unter /Volumes — dort könnte ein
# fremdes Volume gleichen Namens hängen) und den eigenen Arbeitsordner. Die
# Funktion läuft mehrfach (Signal-Trap, danach EXIT-Trap); jede Marke wird nach
# ihrer Behandlung gelöscht, damit der zweite Aufruf nichts mehr tut.
STAGE=""
DMG_DEVICE=""
DMG_ATTACH_STATE="none"
MOUNT_DIR=""
ATTACH_PLIST=""

device_from_attach_plist() {
  local plist="$1" index=0 device mountpoint
  [ -s "$plist" ] || return 1
  while device="$(/usr/libexec/PlistBuddy -c "Print :system-entities:$index:dev-entry" "$plist" 2>/dev/null)"; do
    mountpoint="$(/usr/libexec/PlistBuddy -c "Print :system-entities:$index:mount-point" "$plist" 2>/dev/null || true)"
    if [ "$mountpoint" = "$MOUNT_DIR" ]; then
      printf '%s\n' "$device"
      return 0
    fi
    index=$((index + 1))
  done
  return 1
}

device_from_hdiutil_info() {
  local plist="$STAGE/hdiutil-info.plist" image=0 entity device mountpoint
  hdiutil info -plist > "$plist" 2>/dev/null || return 1
  while /usr/libexec/PlistBuddy -c "Print :images:$image" "$plist" >/dev/null 2>&1; do
    entity=0
    while device="$(/usr/libexec/PlistBuddy -c "Print :images:$image:system-entities:$entity:dev-entry" "$plist" 2>/dev/null)"; do
      mountpoint="$(/usr/libexec/PlistBuddy -c "Print :images:$image:system-entities:$entity:mount-point" "$plist" 2>/dev/null || true)"
      if [ "$mountpoint" = "$MOUNT_DIR" ]; then
        printf '%s\n' "$device"
        return 0
      fi
      entity=$((entity + 1))
    done
    image=$((image + 1))
  done
  return 1
}

aufraeumen() {
  if [ "$DMG_ATTACH_STATE" != "none" ] && [ -z "$DMG_DEVICE" ]; then
    # Ein Signal kann direkt nach `hdiutil attach` eintreffen, bevor der normale
    # Pfad das Device aus der Antwort gelesen hat. Dann wird es über den nur
    # diesem Lauf gehörenden Mountpunkt erneut ermittelt.
    DMG_DEVICE="$(device_from_attach_plist "$ATTACH_PLIST" \
      || device_from_hdiutil_info || true)"
    if [ -z "$DMG_DEVICE" ]; then
      echo "WARNUNG: Device des eigenen Mountpunkts blieb unbekannt; Arbeitsordner wird behalten: $STAGE" >&2
    fi
  fi
  if [ -n "$DMG_DEVICE" ]; then
    if detach_own_device "$DMG_DEVICE"; then
      DMG_DEVICE=""
      DMG_ATTACH_STATE="none"
    else
      echo "WARNUNG: Eigenes DMG-Device ließ sich nicht aushängen: $DMG_DEVICE" >&2
      echo "         Bitte von Hand prüfen: hdiutil info" >&2
    fi
  fi
  # Den Arbeitsordner nur löschen, wenn darin nichts mehr eingehängt ist —
  # sonst zöge das rm die Unterlage eines noch hängenden Volumes weg.
  if [ -n "$STAGE" ] && [ "$DMG_ATTACH_STATE" = "none" ]; then
    rm -rf "$STAGE"
    STAGE=""
  fi
}
trap aufraeumen EXIT
trap 'aufraeumen; exit 130' INT
trap 'aufraeumen; exit 143' TERM

# Alles, was fehlen kann, VOR dem Bauen prüfen: build.sh überspringt eine
# unmögliche Notarisierung still, und der Fehler fiele erst nach minutenlangem
# Bau bei der Ticket-Prüfung auf.
echo "=== 1/4 Voraussetzungen ==="
IDENTITY="$(resolve_signing_identity "$REPO")" || {
  echo "FEHLER: Release benötigt ein eindeutiges Developer-ID-Zertifikat." >&2
  exit 2
}
export CODESIGN_IDENTITY="$IDENTITY"
if ! notary_profile_works "$NOTARY_PROFILE"; then
  echo "FEHLER: Das Notary-Profil '$NOTARY_PROFILE' ist auf diesem Mac nicht verwendbar." >&2
  echo "Über SSH ist der Login-Schlüsselbund gesperrt — dann in einer lokalen" >&2
  echo "Terminalsitzung erneut versuchen. Profil einmal pro Mac anlegen:" >&2
  echo "  xcrun notarytool store-credentials $NOTARY_PROFILE --apple-id <apple-id> --team-id <team-id>" >&2
  exit 2
fi
# Die Version steht in jpegdrop.py (`__version__`); build.sh schreibt sie ins
# Bundle. Hier vor dem Bau aus der Quelle lesen, damit der Endpunkt schon jetzt
# feststeht: Ein vorhandenes Artefakt wird nie überschrieben, und ein
# minutenlanger Bau samt zwei Notarisierungen für ein Image, das ohnehin nicht
# abgelegt werden darf, wäre vertane Zeit. Nach dem Bau wird die Version des
# Bundles dagegen gehalten.
VERSION="$(sed -nE 's/^__version__ = "([^"]+)".*/\1/p' jpegdrop.py | head -1)"
if [ -z "$VERSION" ]; then
  echo "FEHLER: Keine __version__ in jpegdrop.py gefunden." >&2
  exit 1
fi
DMG_FINAL="$DIST/JPEGdrop-$VERSION.dmg"
SHA_FINAL="$DMG_FINAL.sha256"
for ziel in "$DMG_FINAL" "$SHA_FINAL"; do
  if [ -e "$ziel" ] || [ -L "$ziel" ]; then
    echo "FEHLER: $ziel existiert schon — Version erhöhen oder Datei bewusst entfernen." >&2
    exit 1
  fi
done
echo "Identität, Notar-Profil ($NOTARY_PROFILE) und Ziel (Version $VERSION) sind in Ordnung."

echo "=== 2/4 App bauen (inkl. Signatur und Notarisierung) ==="
bash build.sh
assert_release_update_configuration "$APP"
/usr/bin/python3 "$REPO/tools/build_manifest.py" --verify --repo "$REPO" --app "$APP" --version "$VERSION" >/dev/null

# build.sh überspringt die Notarisierung notfalls still — hier wird daraus ein
# harter Fehler, bevor irgendetwas in ein DMG wandert.
if codesign -dvv "$APP" 2>&1 | grep -q '^Signature=adhoc$'; then
  echo "FEHLER: '$APP' ist nur ad-hoc signiert — nichts für ein Release." >&2
  exit 1
fi
xcrun stapler validate "$APP"
assess_gatekeeper "$APP" --type exec
BUNDLE_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist")"
if [ "$BUNDLE_VERSION" != "$VERSION" ]; then
  echo "FEHLER: Bundle trägt Version '$BUNDLE_VERSION', jpegdrop.py sagt '$VERSION'." >&2
  exit 1
fi

echo "=== 3/4 DMG packen ($VERSION) ==="
# Eigener Arbeitsordner je Lauf, im Repo (gitignored): Zwei parallele Läufe
# kommen sich so nicht in die Quere, und ein fester Pfad wäre beim Aufräumen
# nicht sicher der eigene.
STAGE="$(mktemp -d "$REPO/.jpegdrop-release.XXXXXX")"
PAYLOAD="$STAGE/payload"
RW_DMG="$STAGE/JPEGdrop-rw.dmg"
PENDING_DMG="$STAGE/JPEGdrop-$VERSION.dmg"
MOUNT_DIR="$STAGE/mount"
ATTACH_PLIST="$STAGE/attach.plist"

mkdir -p "$PAYLOAD" "$MOUNT_DIR"
/usr/bin/ditto "$APP" "$PAYLOAD/JPEGdrop.app"
cp "$REPO/docs/dmg-readme.txt" "$PAYLOAD/Read Me.txt"
/usr/bin/ditto "$APP/Contents/Resources/Licenses" "$PAYLOAD/Licenses"

# Größe: Payload plus Puffer für Dateisystem und .DS_Store. hdiutil reserviert
# das physisch, also nicht zu großzügig — komprimiert wird erst unten.
PAYLOAD_MB="$(du -sm "$PAYLOAD" | cut -f1)"
case "$PAYLOAD_MB" in
  ''|*[!0-9]*) echo "FEHLER: du hat keine gültige Payload-Größe gemeldet: $PAYLOAD_MB" >&2; exit 1 ;;
esac
DMG_SIZE_MB=$((PAYLOAD_MB + 30))

# HFS+ erhält das Finder-Layout einschließlich der Hintergrunddatei-Aliase.
hdiutil create -srcfolder "$PAYLOAD" -volname "$VOLNAME" -fs HFS+ \
  -fsargs "-c c=64,a=16,e=16" -format UDRW -size "${DMG_SIZE_MB}m" "$RW_DMG"

# Einhängen an einem privaten Mountpunkt dieses Laufs. Das Device kommt aus
# der plist-Ausgabe von hdiutil — nur genau dieses hängt der Lauf wieder aus.
DMG_ATTACH_STATE="attaching"
hdiutil attach "$RW_DMG" -mountpoint "$MOUNT_DIR" \
  -nobrowse -noverify -noautoopen -plist > "$ATTACH_PLIST"
DMG_DEVICE="$(device_from_attach_plist "$ATTACH_PLIST" || true)"
if [ -z "$DMG_DEVICE" ]; then
  echo "FEHLER: Device des eigenen DMG-Mounts nicht in der hdiutil-Ausgabe gefunden." >&2
  exit 1
fi
DMG_ATTACH_STATE="attached"

# Applications-Verknüpfung: Der Finder zeigt sie mit Ordner-Icon und Pfeil.
ln -s /Applications "$MOUNT_DIR/Applications"

if [ "$FINDER_LAYOUT" -eq 1 ]; then
  echo "    Finder-Layout ohne GUI-Fokus setzen"
  "$REPO/.venv/bin/python" -m pip install --quiet -r "$REPO/requirements-build.txt"
  "$REPO/.venv/bin/python" "$REPO/tools/render_dmg_background.py" \
    --source "$REPO/assets/dmg-background.svg" --output "$STAGE/background.tiff"
  "$REPO/.venv/bin/python" "$REPO/tools/dmg_layout.py" \
    --mountpoint "$MOUNT_DIR" --background "$STAGE/background.tiff"
else
  echo "    (Finder-Layout übersprungen: --no-finder-layout)"
fi

sync
if ! detach_own_device "$DMG_DEVICE"; then
  echo "FEHLER: Eigenes DMG-Device ließ sich nicht aushängen: $DMG_DEVICE" >&2
  exit 1
fi
DMG_DEVICE=""
DMG_ATTACH_STATE="none"

hdiutil convert "$RW_DMG" -format UDZO -imagekey zlib-level=9 -o "$PENDING_DMG"
rm -f "$RW_DMG"

echo "=== 4/4 DMG signieren, notarisieren, stapeln, prüfen ==="
# Das DMG selbst signieren, sonst meckert Gatekeeper schon beim Öffnen des
# Images (Quarantäne nach Download).
codesign --force --timestamp --sign "$IDENTITY" "$PENDING_DMG"
xcrun notarytool submit "$PENDING_DMG" --keychain-profile "$NOTARY_PROFILE" --wait
xcrun stapler staple "$PENDING_DMG"
xcrun stapler validate "$PENDING_DMG"
# `--type open`: die Gatekeeper-Regel fürs Öffnen eines Disk-Images. Die App
# darin wurde oben mit `--type exec` bewertet.
assess_gatekeeper "$PENDING_DMG" --type open --context context:primary-signature
hdiutil verify "$PENDING_DMG" >/dev/null

# Die Auslieferung selbst prüfen: Ein geprüftes Quellbundle beweist noch
# nicht, dass dieselbe App mit intakter Signatur im fertigen Image liegt.
echo "    App aus dem fertigen DMG prüfen"
DMG_ATTACH_STATE="attaching"
hdiutil attach "$PENDING_DMG" -readonly -mountpoint "$MOUNT_DIR" \
  -nobrowse -noautoopen -plist > "$ATTACH_PLIST"
DMG_DEVICE="$(device_from_attach_plist "$ATTACH_PLIST" || true)"
if [ -z "$DMG_DEVICE" ]; then
  echo "FEHLER: Device des fertigen DMGs nicht in der hdiutil-Ausgabe gefunden." >&2
  exit 1
fi
DMG_ATTACH_STATE="attached"
IMAGE_APP="$MOUNT_DIR/JPEGdrop.app"
verify_notarized_app "$IMAGE_APP" "$VERSION"
assert_release_update_configuration "$IMAGE_APP"
/usr/bin/python3 "$REPO/tools/build_manifest.py" --verify --repo "$REPO" --app "$IMAGE_APP" --version "$VERSION" >/dev/null
SOURCE_APP_HASH="$(bundle_signature_hash "$APP")"
IMAGE_APP_HASH="$(bundle_signature_hash "$IMAGE_APP")"
if [ "$SOURCE_APP_HASH" != "$IMAGE_APP_HASH" ]; then
  echo "FEHLER: Die App im fertigen DMG unterscheidet sich vom geprüften Build." >&2
  exit 1
fi
detach_own_device "$DMG_DEVICE"
DMG_DEVICE=""
DMG_ATTACH_STATE="none"

# Prüfsumme neben das Image; sie nennt nur den Dateinamen, nicht den Pfad
# dieses Macs, damit `shasum -c` überall funktioniert.
( cd "$STAGE" && shasum -a 256 "$(basename "$PENDING_DMG")" > "$PENDING_DMG.sha256" )

# Erst jetzt, nach allen Prüfungen, an den endgültigen Ort. Der Helfer sperrt
# konkurrierende Läufe, legt beide Namen ohne Überschreiben an und nimmt den
# ersten Namen zurück, falls der zweite Schritt scheitert.
/usr/bin/python3 "$REPO/tools/build_manifest.py" --verify --repo "$REPO" --app "$APP" --version "$VERSION" >/dev/null
if [ "$(git rev-parse HEAD)" != "$SOURCE_COMMIT" ]; then
  echo "FEHLER: Der Quellcommit hat sich während des Release-Laufs geändert." >&2
  exit 2
fi
mkdir -p "$DIST"
if ! publish_release_pair "$PENDING_DMG" "$PENDING_DMG.sha256" \
    "$DMG_FINAL" "$SHA_FINAL" "$DIST/.JPEGdrop-$VERSION.publish-lock"; then
  echo "FEHLER: Veröffentlichung fehlgeschlagen — Artefakte bleiben unter $STAGE." >&2
  STAGE=""   # nicht aufräumen: der Lauf soll nachvollziehbar bleiben
  exit 1
fi

echo "    Größe: $(du -h "$DMG_FINAL" | cut -f1)"
echo "    Prüfen: cd dist && shasum -c $(basename "$SHA_FINAL")"
echo "RELEASE OK: $DMG_FINAL ($VERSION)"
