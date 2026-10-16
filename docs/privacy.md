# Privacy

JPEGdrop converts images locally. It does not upload images, filenames,
conversion results or settings. It has no analytics or crash-reporting service.
Image copies omit embedded EXIF/GPS/ICC data after orientation and color
conversion. Filenames, filesystem timestamps and visible image contents are
not anonymized.

Codec and preset preferences are stored in
`~/Library/Application Support/JPEGdrop/settings.json`. Update preferences are
stored in the app's macOS preferences. The job list is kept in memory for the
current session. Generated images remain next to their originals.

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

Codec und Preset liegen in
`~/Library/Application Support/JPEGdrop/settings.json`, Update-Einstellungen
in den macOS-Einstellungen der App. Die Auftragsliste bleibt nur während der
Sitzung im Speicher. Erzeugte Dateien liegen neben den Originalen.

Automatische und manuell gestartete Update-Prüfungen laden den Feed von
GitHub Pages. Nach Bestätigung wird das Update von GitHub Releases geladen.
GitHub erhält dabei technische Verbindungsdaten wie IP-Adresse und User-Agent.
Die Übermittlung eines Systemprofils ist ausgeschaltet. Automatische Prüfungen
lassen sich in den Einstellungen ausschalten; die manuelle Suche bleibt
möglich. Die Installation erfordert eine Bestätigung.

Weitere Angaben stehen in [GitHubs Datenschutzhinweisen](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement),
im [Impressum](https://dm0.de/impressum.html) und in den
[Datenschutzhinweisen des Herausgebers](https://dm0.de/datenschutz.html).
