#!/usr/bin/env bash
# install.sh — JPEGdrop notarisiert nach /Applications installieren.
#
# Die Einstiegspunkte des Projekts trennen bewusst:
#   bash build.sh   baut die App im Projektverzeichnis; notarisiert, wenn es
#                   geht, überspringt es sonst still (Testbuild soll durchlaufen)
#   ./install.sh    baut und installiert nach /Applications — aber NUR, wenn ein
#                   Notary-Ticket wirklich angeheftet ist
#
# Warum die Strenge hier und nicht in build.sh: In /Applications gehören nur
# Bundles mit angeheftetem Ticket, die Gatekeeper akzeptiert. build.sh darf über
# SSH oder ohne Zertifikat weiterlaufen; eine stillschweigend unnotarisierte App
# in /Applications wäre dagegen genau der Fehler, den man erst Wochen später merkt.
#
# Das Bundle ist seit 2026-08-04 vollständig: Es bringt seinen eigenen
# Python-Interpreter mit und enthält keinen Pfad dieses Macs mehr. Die
# installierte App läuft also unabhängig davon, wo dieses Repo liegt.
# Kehrseite: `git pull` wirkt nicht mehr von selbst auf die installierte App —
# nach jeder Änderung hier erneut `./install.sh` laufen lassen.
#
# Voraussetzungen:
#   - "Developer ID Application"-Zertifikat im Schlüsselbund
#   - notarytool-Profil, Name aus NOTARY_PROFILE oder `git config jpegdrop.notaryProfile`
#     (Default: jpegdrop-notary, wie in build.sh)
#
# Aufruf:  ./install.sh
# Letzte Zeile bei Erfolg: INSTALL OK: /Applications/JPEGdrop.app (<version>)
set -euo pipefail
cd "$(dirname "$0")"
source "$PWD/release-utils.sh"

APP="JPEGdrop.app"
DESTINATION="/Applications/$APP"

# build.sh baut nach JPEGDROP_APP_PATH und ersetzt dieses Ziel am Ende. Fuer den
# Installationslauf wird der Pfad deshalb hier fest auf das Bundle in DIESEM
# Repo gesetzt. Ohne diese Zeile koennte eine von aussen
# gesetzte Variable build.sh an eine ganz andere Stelle bauen lassen — etwa
# direkt nach /Applications, also noch vor Ticket- und Gatekeeper-Pruefung —
# waehrend unten weiterhin das unveraenderte, womoeglich alte JPEGdrop.app
# geprueft und installiert wird.
export JPEGDROP_APP_PATH="$PWD/$APP"

# Profilnamen bestimmen: Umgebung schlägt clone-lokale Git-Konfiguration.
NOTARY_PROFILE="$(resolve_notary_profile "$PWD")"
export NOTARY_PROFILE
CODESIGN_IDENTITY="$(resolve_signing_identity "$PWD")" || {
  echo "FEHLER: Installation benötigt ein eindeutiges Developer-ID-Zertifikat." >&2
  exit 2
}
export CODESIGN_IDENTITY

# Fail-fast VOR dem Bauen: Ein fehlendes Profil erst nach venv-Aufbau und
# Notarisierungsversuch zu bemerken, kostet nur Zeit.
#
# Fünf Versuche statt einem: `notarytool history` meldet gelegentlich fälschlich
# „No Keychain password item found", obwohl das Profil da ist (bei einem früheren Lauf
# belegt — Versuch 1 fehlgeschlagen, Versuch 2 sofort ok). Ein einzelner
# Fehlversuch würde sonst einen ganzen Lauf grundlos abbrechen; ein wirklich
# fehlendes Profil scheitert auch nach fünf Versuchen.
if ! notary_profile_works "$NOTARY_PROFILE"; then
  echo "FEHLER: Das Notary-Profil '$NOTARY_PROFILE' ist auf diesem Mac nicht verwendbar." >&2
  echo "Über SSH ist der Login-Schlüsselbund gesperrt — dann in einer lokalen" >&2
  echo "Terminalsitzung erneut versuchen. Profil einmal pro Mac anlegen:" >&2
  echo "  xcrun notarytool store-credentials $NOTARY_PROFILE --apple-id <apple-id> --team-id <team-id>" >&2
  exit 2
fi

echo "=== 1/3 App bauen (inkl. Signatur und Notarisierung) ==="
bash build.sh

# Gatekeeper-Urteil einholen und ERZWINGEN.
#
# Früher stand `spctl` links von einer `tail`-Pipe. Der Status einer Pipeline ist
# der ihres LETZTEN Glieds — also der von `tail` und damit immer 0. Eine
# Gatekeeper-Ablehnung fiel dadurch still durch, und der Lauf meldete anschließend
# trotzdem "INSTALL OK". Deshalb: erst die Ausgabe einsammeln, dann den Status
# prüfen, und die gekürzte Ausgabe danach zeigen.
#
# `pipefail` allein reicht hier nicht: Es würde zwar den spctl-Status
# durchreichen, aber `tail` beendet sich schon vorher regulär — der Aufruf muss
# ohne Pipe laufen, damit der Status überhaupt entsteht.
echo "=== 2/3 Ticket prüfen ==="
# build.sh überspringt die Notarisierung notfalls still — hier wird daraus ein
# harter Fehler, bevor irgendetwas nach /Applications wandert.
if codesign -dvv "$APP" 2>&1 | grep -q '^Signature=adhoc$'; then
  echo "FEHLER: '$APP' ist nur ad-hoc signiert — nichts für /Applications." >&2
  exit 1
fi
xcrun stapler validate "$APP"
assess_gatekeeper "$APP" --type exec

VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist")"

echo "=== 3/3 Installieren ==="
# Erst neben das Ziel legen, dann atomar austauschen: ein Abbruch mittendrin
# darf keine halb ersetzte App in /Applications hinterlassen.
STAGED="/Applications/.$APP.install-$$"
rm -rf "$STAGED"
trap 'rm -rf "$STAGED"' EXIT
ditto "$APP" "$STAGED"
# Die laufende Instanz ist ein Python-Prozess, kein Prozess namens JPEGdrop —
# deshalb über den Skriptpfad beenden statt über den Bundle-Namen.
#
# Gesucht werden zwei vollstaendige Pfade: der aus der installierten App (die
# gleich ersetzt wird) und der aus diesem Repo (eine direkt gestartete
# Entwicklungsinstanz). Seit das Bundle seinen eigenen Interpreter mitbringt,
# laeuft die installierte App unter ihrem EIGENEN Pfad in /Applications — der
# Repo-Pfad allein wuerde sie nicht mehr finden und eine laufende Instanz
# ungebremst weiterlaufen lassen, waehrend ihr Bundle unter ihr ausgetauscht
# wird. Das fruehere `pkill -f "app/menu_app.py"` traf umgekehrt jeden Prozess,
# dessen Argumente diesen Teilstring enthalten — also auch Instanzen aus anderen
# Klonen und beliebige fremde Prozesse.
#
# Vier Praezisierungen gegenueber dem alten Aufruf:
#   1. Der Vergleich laeuft ueber ein literales case-Muster statt ueber einen
#      regulaeren Ausdruck — Sonderzeichen im Repo-Pfad koennen ihn also nicht
#      verbiegen.
#   2. Nur Python-Prozesse kommen infrage, und der Pfad muss darin ein
#      eigenstaendiges Argument sein. Ein Editor, Pager oder grep mit demselben
#      Pfad als Argument wird dadurch nicht getroffen.
#   3. Gesucht wird auch der Pfad der KONVERTIER-CLI (jpegdrop.py). Die
#      Menueleisten-App startet sie fuer jeden Drop als eigenen Prozess; deren
#      Argumentliste nennt jpegdrop.py, nicht menu_app.py. Ohne diesen Eintrag
#      lief eine Konvertierung weiter, waehrend ihr Bundle ausgetauscht wurde.
#   4. Nach dem Signal wird auf das tatsaechliche Ende gewartet. `kill` schickt
#      nur SIGTERM und kehrt sofort zurueck — der Austausch waere sonst wieder
#      ein Rennen gegen einen noch laufenden Prozess. Endet einer nicht
#      innerhalb der Frist, bricht die Installation ab, statt ihm das Bundle
#      unter den Fuessen wegzuziehen.
STOP_TIMEOUT="${JPEGDROP_STOP_TIMEOUT:-10}"   # Sekunden; fuer Tests umstellbar
case "$STOP_TIMEOUT" in
  ''|*[!0-9]*)
    echo "HINWEIS: JPEGDROP_STOP_TIMEOUT='$STOP_TIMEOUT' ist ungueltig; verwende 10s." >&2
    STOP_TIMEOUT=10
    ;;
esac
if [ "$STOP_TIMEOUT" -lt 1 ]; then
  echo "HINWEIS: JPEGDROP_STOP_TIMEOUT muss mindestens 1 sein; verwende 1s." >&2
  STOP_TIMEOUT=1
fi
stop_repo_instances() {
  local snapshot pid executable command script_path wartezeit kill_fehler
  local -a getroffen=() offen=()
  snapshot="$(ps -axo pid=,ucomm=,command=)"
  while read -r pid executable command; do
    # Den eigenen Prozess nie beenden: je nach Aufrufart koennen die gesuchten
    # Pfade auch in der Argumentliste dieses Skripts auftauchen.
    if [ "$pid" = "$$" ]; then
      continue
    fi
    # `ucomm` ist der tatsaechliche Prozessname. Erst damit wird aus einem Pfad
    # in der Argumentliste eine laufende JPEGdrop-Python-Instanz statt etwa ein
    # Editor, `less`, `git diff` oder `grep` auf derselben Datei.
    case "${executable##*/}" in
      python|python[0-9]*|Python|Python[0-9]*) ;;
      *) continue ;;
    esac
    for script_path in "$@"; do
      local matched=0
      # `ps` liefert eine flache Kommandozeile. Darin bleibt der vollständige
      # Pfad als Zeichenfolge erhalten, auch wenn er Leerzeichen enthält. Ein
      # früheres Python-Skript oder -c/-m kennzeichnet dagegen ein fremdes Tool,
      # dem der JPEGdrop-Pfad nur als späteres Argument übergeben wurde.
      case " $command " in
        *" $script_path "*)
          local prefix="${command%%"$script_path"*}"
          case " $prefix " in
            *" -c "*|*" -m "*|*".py "*) matched=0 ;;
            *) matched=1 ;;
          esac
          ;;
      esac
      if [ "$matched" -eq 1 ]; then
        kill_fehler=""
        if ! kill_fehler="$(kill "$pid" 2>&1)"; then
          # Ein Prozess kann zwischen ps-Snapshot und kill regulär enden.
          # Existiert die PID dagegen weiterhin, fehlt typischerweise die
          # Berechtigung (anderer Benutzer/root): Dann darf die Installation
          # nicht so tun, als sei die Instanz beendet.
          if ps -p "$pid" -o pid= >/dev/null 2>&1; then
            echo "FEHLER: JPEGdrop-Prozess $pid konnte nicht beendet werden." >&2
            [ -n "$kill_fehler" ] && echo "  $kill_fehler" >&2
            return 1
          fi
          break
        fi
        getroffen+=("$pid")
        break
      fi
    done
  done <<< "$snapshot"

  if [ "${#getroffen[@]}" -eq 0 ]; then
    return 0
  fi
  # In 0,2-s-Schritten nachsehen, wer schon weg ist (`kill -0` sendet kein
  # Signal, es prueft nur, ob der Prozess noch existiert).
  wartezeit=0
  while :; do
    offen=()
    for pid in "${getroffen[@]}"; do
      if kill -0 "$pid" 2>/dev/null; then
        offen+=("$pid")
      elif ps -p "$pid" -o pid= >/dev/null 2>&1; then
        echo "FEHLER: Status von JPEGdrop-Prozess $pid ist nicht pruefbar." >&2
        return 1
      fi
    done
    if [ "${#offen[@]}" -eq 0 ]; then
      return 0
    fi
    if [ "$wartezeit" -ge "$((STOP_TIMEOUT * 5))" ]; then
      break
    fi
    sleep 0.2
    wartezeit=$((wartezeit + 1))
  done
  echo "FEHLER: Diese JPEGdrop-Prozesse laufen nach ${STOP_TIMEOUT}s noch: ${offen[*]}" >&2
  echo "  Vermutlich laeuft gerade eine Konvertierung. Abwarten oder die" >&2
  echo "  Prozesse beenden, dann erneut installieren." >&2
  return 1
}
stop_repo_instances \
  "$DESTINATION/Contents/Resources/app/menu_app.py" \
  "$DESTINATION/Contents/Resources/jpegdrop.py" \
  "$PWD/$APP/Contents/Resources/app/menu_app.py" \
  "$PWD/$APP/Contents/Resources/jpegdrop.py" \
  "$PWD/app/menu_app.py" \
  "$PWD/jpegdrop.py"
/usr/bin/swift - "$STAGED" "$DESTINATION" <<'SWIFT'
import Foundation

let fileManager = FileManager.default
let source = URL(fileURLWithPath: CommandLine.arguments[1])
let destination = URL(fileURLWithPath: CommandLine.arguments[2])
if fileManager.fileExists(atPath: destination.path) {
    _ = try fileManager.replaceItemAt(
        destination,
        withItemAt: source,
        backupItemName: nil,
        options: [.usingNewMetadataOnly]
    )
} else {
    try fileManager.moveItem(at: source, to: destination)
}
SWIFT
trap - EXIT

# Nach dem Kopieren erneut prüfen: erst dann ist die Installation belegt.
xcrun stapler validate "$DESTINATION"
assess_gatekeeper "$DESTINATION" --type exec

echo "INSTALL OK: $DESTINATION ($VERSION)"
