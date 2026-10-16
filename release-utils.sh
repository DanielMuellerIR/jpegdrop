#!/usr/bin/env bash
# Gemeinsame, headless testbare Schutzfunktionen für Installation und Release.

resolve_notary_profile() {
  local configured
  configured="${NOTARY_PROFILE:-$(git -C "$1" config --local --get jpegdrop.notaryProfile 2>/dev/null || true)}"
  printf '%s\n' "${configured:-jpegdrop-notary}"
}

resolve_signing_identity() {
  local requested identities candidates matches count
  requested="${CODESIGN_IDENTITY:-$(git -C "$1" config --local --get jpegdrop.codesignIdentity 2>/dev/null || true)}"
  identities="$(security find-identity -v -p codesigning 2>/dev/null)" || return 1
  candidates="$(printf '%s\n' "$identities" | sed -nE 's/^[[:space:]]*[[:digit:]]+\) ([[:xdigit:]]{40}) "(Developer ID Application:.*)"$/\1\t\2/p')"
  if [ -n "$requested" ]; then
    matches="$(printf '%s\n' "$candidates" | awk -F '\t' -v wanted="$requested" '$2 == wanted || toupper($1) == toupper(wanted)')"
  else
    matches="$candidates"
  fi
  count="$(printf '%s\n' "$matches" | awk 'NF { n++ } END { print n+0 }')"
  if [ "$count" -eq 1 ]; then
    # Die Zertifikat-Prüfsumme wählt auch bei erneuerten Zertifikaten mit
    # identischem Namen eindeutig dasselbe Zertifikat in allen Bauphasen.
    printf '%s\n' "$matches" | cut -f1
    return 0
  fi
  if [ "$count" -eq 0 ] && [ -z "$requested" ]; then
    return 1
  fi
  echo "FEHLER: Developer-ID-Zertifikat fehlt oder ist mehrdeutig. CODESIGN_IDENTITY oder jpegdrop.codesignIdentity eindeutig setzen." >&2
  return 2
}

assert_release_update_configuration() {
  local app="$1" info="$1/Contents/Info.plist" feed marker
  marker="$(/usr/libexec/PlistBuddy -c 'Print :JPEGdropTestUpdateFeed' "$info" 2>/dev/null || true)"
  feed="$(/usr/libexec/PlistBuddy -c 'Print :SUFeedURL' "$info" 2>/dev/null || true)"
  if [ "$marker" = "true" ] || [[ "$feed" != https://* ]]; then
    echo "FEHLER: '$app' enthält keine öffentliche HTTPS-Update-Konfiguration." >&2
    return 2
  fi
}

verify_notarized_app() {
  local app="$1" expected_version="$2" info="$1/Contents/Info.plist" identifier version build_version
  codesign --verify --deep --strict "$app" || return 1
  xcrun stapler validate "$app" || return 1
  assess_gatekeeper "$app" --type exec || return 1
  identifier="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$info")" || return 1
  version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$info")" || return 1
  build_version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$info")" || return 1
  if [ "$identifier" != "de.dm0.jpegdrop" ] || [ "$version" != "$expected_version" ] || [ "$build_version" != "$expected_version" ]; then
    echo "FEHLER: Die geprüfte App trägt nicht die erwartete JPEGdrop-Identität und Version." >&2
    return 1
  fi
}

bundle_signature_hash() {
  local details hash
  details="$(codesign --display --verbose=4 "$1" 2>&1)" || return 1
  hash="$(printf '%s\n' "$details" | sed -n 's/^CDHash=//p')"
  [ -n "$hash" ] || return 1
  printf '%s\n' "$hash"
}
notary_profile_works() {
  local profile="$1" attempt
  # notarytool kann ein vorhandenes Profil vorübergehend nicht finden. Fünf
  # begrenzte Versuche vermeiden einen unnötigen Abbruch, ohne endlos zu warten.
  for attempt in 1 2 3 4 5; do
    xcrun notarytool history --keychain-profile "$profile" >/dev/null 2>&1 && return 0
    [ "$attempt" -lt 5 ] && sleep 3
  done
  return 1
}

assess_gatekeeper() {
  local ziel="$1" ausgabe rc=0
  shift
  # Erst den echten spctl-Status sichern, dann die lange Ausgabe kürzen. Eine
  # Pipe würde sonst nur den Status von `tail` melden.
  ausgabe="$(spctl --assess "$@" -vv "$ziel" 2>&1)" || rc=$?
  printf '%s\n' "$ausgabe" | tail -2
  if [ "$rc" -ne 0 ]; then
    echo "FEHLER: Gatekeeper lehnt '$ziel' ab (spctl-Status $rc)." >&2
    return 1
  fi
}

reserve_release_file() {
  # os.link behandelt auch ein inzwischen entstandenes Verzeichnis oder
  # einen Symlink als belegten Zielnamen; ln würde in ein Verzeichnis kopieren.
  /usr/bin/python3 -c 'import os, sys; os.link(sys.argv[1], sys.argv[2], follow_symlinks=False)' "$1" "$2"
}

publish_release_pair() (
  set -euo pipefail
  quelle_dmg="$1"; quelle_sha="$2"; ziel_dmg="$3"; ziel_sha="$4"; sperre="$5"
  sperre_gehalten=0; erstes_ziel=0; zweites_ziel=0; paar_fertig=0

  aufraeumen_veroeffentlichung() {
    if [ "$erstes_ziel" -eq 1 ] && [ "$paar_fertig" -eq 0 ] && [ "$quelle_dmg" -ef "$ziel_dmg" ]; then
      unlink "$ziel_dmg" 2>/dev/null || true
    fi
    if [ "$zweites_ziel" -eq 1 ] && [ "$paar_fertig" -eq 0 ] && [ "$quelle_sha" -ef "$ziel_sha" ]; then
      unlink "$ziel_sha" 2>/dev/null || true
    fi
    if [ "$sperre_gehalten" -eq 1 ]; then
      rmdir "$sperre" 2>/dev/null || true
    fi
  }
  trap aufraeumen_veroeffentlichung EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  if ! mkdir "$sperre" 2>/dev/null; then
    echo "FEHLER: Ein anderer Release-Lauf veröffentlicht diese Version bereits: $sperre" >&2
    return 1
  fi
  sperre_gehalten=1
  for ziel in "$ziel_dmg" "$ziel_sha"; do
    if [ -e "$ziel" ] || [ -L "$ziel" ]; then
      echo "FEHLER: $ziel ist inzwischen entstanden und wird nicht überschrieben." >&2
      return 1
    fi
  done

  # Hardlinks reservieren beide Namen ohne Überschreiben. Stage und dist
  # liegen im selben Repo und damit auf demselben Dateisystem.
  erstes_ziel=1
  reserve_release_file "$quelle_dmg" "$ziel_dmg" || return 1
  zweites_ziel=1
  reserve_release_file "$quelle_sha" "$ziel_sha" || return 1
  paar_fertig=1
  unlink "$quelle_dmg" || return 1
  unlink "$quelle_sha" || return 1
)
