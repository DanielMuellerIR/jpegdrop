# Privacy

JPEGdrop converts images locally. It does not upload images, filenames,
conversion results or settings. It has no analytics or crash-reporting service.
Image copies omit embedded EXIF/GPS/ICC data after orientation and color
conversion. Filenames, filesystem timestamps and visible image contents are
not anonymized.

Image options and chosen output paths are stored in
`~/Library/Application Support/JPEGdrop/settings.json`. Update preferences are
stored in the app's macOS preferences. The job list is kept in memory for the
current session. Generated images remain in the selected output location. A private temporary file list is removed at normal job completion; a forced process termination can leave temporary lists.

The optional comparison preview keeps a private source copy, decoded comparison
images, encoded output and its option snapshot in the system temporary directory.
It does not save Settings or write to regular output locations. Recalculation,
cancellation, closing and normal app shutdown remove those files. A forced system
kill can leave temporary preview data; it is never uploaded.

Named profiles, including their names and output paths, are stored locally in
`~/Library/Application Support/JPEGdrop/profiles.json`. The active profile ID is
part of `settings.json`; queued jobs keep a name and option snapshot in memory.
The optional Finder workflow and its integrity receipt live in
`~/Library/Services/Mit JPEGdrop konvertieren.workflow`. The workflow passes
selected file paths to the installed app through macOS LaunchServices. It does
not upload data or control Finder through AppleScript.

When automatic update checks are enabled, or you choose “Nach Updates suchen…”,
Sparkle contacts the JPEGdrop feed on GitHub Pages. A confirmed update downloads
its archive from GitHub Releases. These requests reveal connection information
such as your IP address and the update client's user agent to GitHub. System
profile submission is disabled. You can disable automatic checks in Settings;
manual checks remain available. Installation requires confirmation.

See [GitHub's privacy statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement)
for its handling of access data, and the publisher's
[legal notice](https://dm0.de/impressum.html) and
[privacy information](https://dm0.de/datenschutz.html) for contact and rights.

## Deutsch

JPEGdrop verarbeitet Bilder lokal. Bilder, Dateinamen, Ergebnisse und
Einstellungen werden nicht hochgeladen. Es gibt keine Analyse- oder
Absturzmeldedienste. Eingebettete EXIF-/GPS-/ICC-Daten werden nach der
Orientierungs- und Farbumrechnung entfernt. Dateinamen, Dateisystem-Zeitstempel
und sichtbare Bildinhalte werden nicht anonymisiert.

Bildoptionen und gewählte Ausgabepfade liegen in
`~/Library/Application Support/JPEGdrop/settings.json`, Update-Einstellungen
in den macOS-Einstellungen der App. Die Auftragsliste bleibt nur während der
Sitzung im Speicher. Erzeugte Dateien liegen in der gewählten Ablage. Eine private temporäre Dateiliste wird beim regulären Abschluss gelöscht; ein harter Prozessabbruch kann Listenreste hinterlassen.

Die optionale Vergleichsvorschau hält eine private Quellkopie, dekodierte
Vergleichsbilder, die kodierte Ausgabe und den Optionssnapshot im temporären
Systemverzeichnis. Sie speichert keine Einstellungen und schreibt nicht in
reguläre Zielablagen. Neuberechnung, Abbruch, Schließen und normales Beenden
entfernen diese Dateien. Ein erzwungener Systemabbruch kann temporäre
Vorschaudaten hinterlassen; sie werden nie hochgeladen.

Benannte Profile einschließlich Namen und Ausgabepfaden liegen lokal in
`~/Library/Application Support/JPEGdrop/profiles.json`. Die aktive Profil-ID
gehört zu `settings.json`; Aufträge halten Namen und Optionen als Snapshot im
Speicher. Der optionale Finder-Workflow und sein Integritätsbeleg liegen unter
`~/Library/Services/Mit JPEGdrop konvertieren.workflow`. Er übergibt gewählte
Dateipfade über macOS LaunchServices an die installierte App. Er lädt keine
Daten hoch und steuert Finder nicht über AppleScript.

Automatische und manuell gestartete Update-Prüfungen laden den Feed von
GitHub Pages. Nach Bestätigung wird das Update von GitHub Releases geladen.
GitHub erhält dabei technische Verbindungsdaten wie IP-Adresse und User-Agent.
Die Übermittlung eines Systemprofils ist ausgeschaltet. Automatische Prüfungen
lassen sich in den Einstellungen ausschalten; die manuelle Suche bleibt
möglich. Die Installation erfordert eine Bestätigung.

Weitere Angaben stehen in [GitHubs Datenschutzhinweisen](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement),
im [Impressum](https://dm0.de/impressum.html) und in den
[Datenschutzhinweisen des Herausgebers](https://dm0.de/datenschutz.html).
