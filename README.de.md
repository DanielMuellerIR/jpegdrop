# JPEGdrop

**🌐 Sprache / Language:** [English](README.md) · [Deutsch](README.de.md)

JPEGdrop erzeugt aus Bildern auf seinem macOS-Dock-Icon kleinere, metadatenfreie JPEG-, PNG-, AVIF- oder WebP-Kopien, standardmäßig neben den Originalen.

## Installieren und verwenden

1. Das DMG unter [Releases](https://github.com/danielmuellerir/jpegdrop/releases) herunterladen.
2. JPEGdrop nach Programme ziehen und öffnen. Python und Homebrew sind nicht nötig.
3. Im **JD**-Menü oder unter **Einstellungen…** (⌘,) Codec und Preset wählen.
4. Bilder oder Ordner auf das Dock- oder Finder-Icon der App ziehen.

Die Oberfläche ist deutsch. Standard ist **JPEG · hoch** (Qualität 85).
JPEGdrop wird ausschließlich für Apple Silicon gebaut. Das Bundle weist macOS
12.0 als Mindestversion aus; Release-Tests liefen auf macOS 26.7.1. Die Ausführung
auf macOS 12 ist noch nicht belegt. Getestete Systeme stehen in den Release Notes.

![JPEGdrop-Einstellungen](assets/settings.png)

## Dateien und Bildverarbeitung

Originale werden weder verändert noch überschrieben. Die Kopien liegen daneben:

| Quelle | JPEG-Ausgabe |
|---|---|
| `foto.heic` oder `foto.png` | `foto.jpg` |
| `foto.jpg` | `foto-klein.jpg` |
| Ein Zielname existiert bereits | `foto-2.jpg`, dann `foto-3.jpg` usw. |

HEIC/HEIF, JPEG, PNG, TIFF, WebP, AVIF und weitere von Pillow unterstützte
Einzelbilder lassen sich einlesen. HEIC ist ein Eingabeformat; JPEGdrop erzeugt
keine HEIC-Dateien.

- EXIF-Orientierung wird eingerechnet, bevor eingebettete EXIF-/GPS-/ICC-Daten entfernt werden.
- Unterstützte Farbprofile werden nach sRGB umgerechnet. Ungültige oder nicht
  zum Bild passende Profile führen zu einem Fehler statt zu ungeprüfter Ausgabe.
- PNG, AVIF und WebP können Transparenz erhalten. Standardmäßig wird sie auf
  Weiß gelegt; die Hintergrundfarbe lässt sich in den Einstellungen wählen.
  JPEG entfernt Transparenz immer. Eine Warnung nennt betroffene Bilder.
- Eine maximale Bildkante verkleinert optional proportional mit Lanczos, nach
  dem Einrechnen der Orientierung. Kleine Bilder werden niemals vergrößert.
- Animationen und Mehrbilddateien werden abgewiesen. Das erste Bild wird nicht
  unbemerkt als Einzelbild exportiert.
- Dateinamen, Dateisystem-Zeitstempel und sichtbare Bildinhalte werden nicht anonymisiert.

JPEG, AVIF und WebP bieten sechs feste Qualitätsstufen sowie **auto**. Auto passt
die Qualität an die längere Kante des vorbereiteten Bildes an, nach optionaler Verkleinerung.
PNG bietet verlustfreie Kodierung oder eine reduzierte Palette mit 256 Farben.
„Verlustfrei“ beschreibt die PNG- oder WebP-Kodierung nach den gewählten Farb-,
Transparenz- und Größenoptionen; diese Vorbereitung kann die Quellpixel ändern.
WebP bietet ein echtes verlustfreies Preset, das alle vorbereiteten Pixel auch
in transparenten Bereichen erhält. Das PNG-Preset mit 256 Farben kann Farb-
und Alphastufen reduzieren.
Auch das höchste JPEG-Preset arbeitet verlustbehaftet. Schon optimierte Bilder
können größer werden; Dateigrößen und Warnungen werden entsprechend angezeigt.

## Ordner und Ausgabeablage

Ordnerdrops erfassen Bilder auf der obersten Ebene. **Unterordner einbeziehen**
aktiviert verschachtelte Ordner. JPEGdrop erstellt das Inventar vor der
Konvertierung und überspringt symbolische Links, doppelte Dateien, App-Pakete
und verwaltete Fotomediatheken. Ungeeignete Einträge werden gemeldet. Die
Inventur ist auf 10.000 Bilder und höchstens 50.000 untersuchte Dateien/Ordner
begrenzt; eine Überschreitung beendet den Auftrag vor der Konvertierung.

Die Ausgabeablage lässt sich in den Einstellungen wählen:

| Einstellung | Beispiel für `Album/Unterordner/foto.png` als JPEG |
|---|---|
| Neben der Quelle (Standard) | `Album/Unterordner/foto.jpg` |
| Ausgabe-Unterordner `JPEGdrop` | `Album/JPEGdrop/Unterordner/foto.jpg` |
| Gewählter Zielordner `Exporte` | `Exporte/Album/Unterordner/foto.jpg` |

Einzeldateien landen direkt im Zielordner oder im Unterordner ihres
Quellverzeichnisses. Die Ordnerstruktur bleibt erhalten. Ein gewähltes Ziel im
Quellbaum wird beim Einlesen ausgelassen. Andere vorhandene Bilder bleiben
Eingaben, auch frühere Konvertierungen. Bei Kollisionen wird hochgezählt;
Quellen und bestehende Ausgaben bleiben unverändert. Bei überlappenden
Eingaben zählt das erste Vorkommen.

## Aufträge, Fortschritt und Abbruch

**Aufträge…** (⌘0) öffnet die Liste mit Fortschritt, aktuellem Dateinamen,
Ergebnissen, Warnungen und Fehlern. Drops werden nacheinander verarbeitet;
bis zu 20 Aufträge können laufen oder warten. Jeder Auftrag behält die beim
Drop gewählten Einstellungen. Ungeeignete Dateien gemischter Drops erscheinen
als übersprungen.

**Auftrag abbrechen** beendet einen Auftrag. Fertige Kopien bleiben erhalten,
unvollständige temporäre Ausgaben werden entfernt. **Erzeugte Dateien im Finder**
wählt vorhandene Ausgaben des gewählten Auftrags aus, auch bei Teilerfolg.
Beim Beenden fragt die App vor dem Abbruch aktiver Aufträge und wartet auf ihre
Encoder-Prozesse. Bei einem erzwungenen Prozessabbruch kann sie kein Aufräumen
garantieren. Die Liste hält bis zu 50 abgeschlossene Aufträge während der
Sitzung vor und ist nach dem Neustart leer. Große Inventare entfernen ältere
Aufträge früher aus der Liste; der letzte Auftrag bleibt vollständig.

## Updates und Datenschutz

**Nach Updates suchen…** prüft auf signierte Sparkle-Updates. Automatische
Prüfungen lassen sich in den Einstellungen ausschalten. Die Installation
erfordert eine Bestätigung und wartet auf aktive Konvertierungen. Während
einer anstehenden Installation sind neue Drops gesperrt.

Bilder werden lokal verarbeitet und nie hochgeladen. Update-Anfragen gehen an
GitHub Pages/Releases; die Übermittlung eines Systemprofils ist ausgeschaltet.
**Datenschutz…** im App-Menü öffnet die mitgelieferten Hinweise auch offline.
Weitere Angaben stehen in den [Datenschutzhinweisen](docs/privacy.md) und im
[Impressum](https://dm0.de/impressum.html). Probleme lassen sich unter
[Issues](https://github.com/danielmuellerir/jpegdrop/issues) melden.

## CLI

Aus einem Quellcheckout mit installierten Abhängigkeiten:

```sh
.venv/bin/python jpegdrop.py --codec avif --preset mittel foto.heic
.venv/bin/python jpegdrop.py --json --codec jpeg foto.png
.venv/bin/python jpegdrop.py --codec webp --preset verlustfrei --preserve-alpha --max-edge 1600 foto.png
.venv/bin/python jpegdrop.py --recursive --output-dir Exporte --codec jpeg Album
.venv/bin/python jpegdrop.py --output-subdir JPEGdrop --codec webp foto.png
.venv/bin/python jpegdrop.py --list-presets
.venv/bin/python jpegdrop.py --print-settings-path
```

Auch die heruntergeladene App enthält eine headless nutzbare CLI:

```sh
"/Applications/JPEGdrop.app/Contents/Resources/python/bin/python3" -I -B \
  "/Applications/JPEGdrop.app/Contents/Resources/jpegdrop.py" --json foto.heic
```

Ohne explizite Optionsflags gelten die gespeicherten App-Einstellungen.
`--max-edge 0` deaktiviert eine gespeicherte Größenbegrenzung. `--flatten-alpha`
überschreibt gespeicherte Alpha-Erhaltung, `--background '#123456'` wählt einen
opaken sRGB-Hintergrund.
`--suffix` ändert das Standardsuffix `-klein` bei Quellen, die schon im Ziel-Codec
vorliegen. `-q/--quality` bleibt für JPEG mit fester Qualität verfügbar.
Der Exit-Status ist die Anzahl Fehler, begrenzt auf 125; null bedeutet Erfolg.

`--no-recursive` und `--beside` überschreiben gespeicherte Ordner-/Ablageoptionen.
Für große Dateilisten nimmt `--input-plan plan.json` ein JSON-Array aus Objekten
mit absolutem `source` und absolutem oder null gesetztem `output_directory` an.
Es ersetzt Positionsargumente, Rekursion und Ablageflags; Bildoptionen gelten
weiterhin. Ein Plan ist auf 10.000 Dateien und 32 MiB begrenzt.

Versionen ohne Update-Menü benötigen einmalig die manuelle Installation des aktuellen DMGs.

## Bauen und prüfen

Python 3.14 und Xcode Command Line Tools auf macOS verwenden:

```sh
./build.sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/ -q
bash -n build.sh install.sh release.sh release-utils.sh
```

`build.sh` erzeugt ein eigenständiges Bundle im Checkout. Bei passender
Konfiguration signiert es mit Developer ID und notarisiert; andernfalls entsteht
ein Entwicklungsbuild. `install.sh` ersetzt eine Installation nur mit gültigem
Notary-Ticket. `release.sh` benötigt einen sauberen Quellcommit und erzeugt ein
signiertes, notarisiertes DMG samt SHA256-Datei, ohne die App zu installieren.
Details stehen in der [Release-Anleitung](docs/release.md).

Der eigene JPEGdrop-Code steht unter [MIT](LICENSE). Gebündelte Abhängigkeiten
behalten [ihre eigenen Lizenzen](licenses/README.md); die Hinweise liegen App und DMG bei.
