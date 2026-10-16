# JPEGdrop

**🌐 Sprache / Language:** [English](README.md) · [Deutsch](README.de.md)

JPEGdrop converts images dropped onto its macOS Dock icon into smaller, metadata-free JPEG, PNG, AVIF or WebP copies, by default beside the originals.

## Install and use

1. Download the DMG from [Releases](https://github.com/danielmuellerir/jpegdrop/releases).
2. Drag JPEGdrop to Applications and open it. Python and Homebrew are not required.
3. Choose a profile, or codec and preset, from the **JD** menu or **Einstellungen…** (⌘,).
4. Drop images or folders onto the app's Dock or Finder icon.

The interface currently uses German labels. The default is **JPEG · hoch**
(quality 85). JPEGdrop builds for Apple Silicon only. The bundle declares macOS
12.0 as its minimum; release tests have run on macOS 26.7.1. Execution on macOS
12 has not yet been verified. See the release notes for the tested systems.

![JPEGdrop settings](assets/settings.png)

## Files and image handling

Originals are never changed or overwritten. Copies are saved next to them:

| Source | JPEG output |
|---|---|
| `photo.heic` or `photo.png` | `photo.jpg` |
| `photo.jpg` | `photo-klein.jpg` |
| An output name already exists | `photo-2.jpg`, then `photo-3.jpg`, etc. |

HEIC/HEIF, JPEG, PNG, TIFF, WebP, AVIF and other Pillow-supported still images
can be read. HEIC is an input format; JPEGdrop does not produce HEIC files.

- EXIF orientation is applied before embedded EXIF/GPS/ICC metadata is removed.
- Supported color profiles are converted to sRGB. Invalid or incompatible
  profiles cause an error rather than an unchecked output.
- PNG, AVIF and WebP can preserve transparency. By default, transparency is
  flattened onto white; a custom background color is available in Settings.
  JPEG always removes transparency. A warning reports affected images.
- A maximum image edge optionally resizes images proportionally with Lanczos,
  after orientation is applied. Smaller images are never enlarged.
- Animated or multi-image files are rejected. The first frame is not silently
  exported as a still image.
- Filenames, filesystem timestamps and visible image contents are not anonymized.

JPEG, AVIF and WebP offer six fixed quality levels and **auto**, which adapts
quality to the prepared image's longer edge, after optional resizing.
PNG offers lossless encoding or a reduced 256-color palette. “Lossless” describes
PNG or WebP encoding after the selected color, transparency and size options;
those preparation steps may change source pixels. WebP also has a true lossless
preset that preserves all prepared pixels, including transparent ones. The
256-color PNG preset can reduce both color and alpha levels. The highest JPEG preset still uses
lossy encoding. An already optimized image may become larger; sizes and warnings
are reported honestly.

## Folders and output location

Folder drops include images from the top level. Enable **Unterordner einbeziehen**
to include nested folders. JPEGdrop inventories the input before conversion,
skips symbolic links, duplicate files, app packages and managed photo libraries,
and reports unsupported entries. Limits are 10,000 images and at most 50,000
examined files/directories; exceeding a limit stops before any conversion.

Choose the output location in Settings:

| Setting | Example for `Album/nested/photo.png` converted to JPEG |
|---|---|
| Beside the source (default) | `Album/nested/photo.jpg` |
| Output subfolder `JPEGdrop` | `Album/JPEGdrop/nested/photo.jpg` |
| Chosen directory `Exports` | `Exports/Album/nested/photo.jpg` |

Individual files go directly into the chosen directory, or a subfolder of their
source directory. Folder structure is retained. A chosen output directory inside
the input tree is excluded from scanning. Existing images elsewhere in the tree
remain valid inputs, including previous conversions. Collisions receive a counter;
no source or existing output is overwritten. Overlapping inputs use the first occurrence.

## File-size limits

An optional **Zielgröße** limits each output file's actual bytes. Settings use
KB = 1,000 bytes; zero disables the limit. JPEG, AVIF and lossy WebP test quality
from the selected preset's maximum down to q15, keeping the first candidate
that fits. Every higher quality is checked; file size is not assumed monotonic.
PNG and lossless WebP keep their encoding method. Legacy `--quality` stays exact.

Image dimensions take priority. Further resizing requires **Für Zielgröße weiter
verkleinern**, with a minimum longer edge (default 320 pixels). At most four
sizes and 81 qualities per size are checked. Each candidate starts from the
same prepared pixels. The search can be cancelled and reports its progress.
If no allowed candidate fits, JPEGdrop reports `target_size_unreachable` and
creates no output. It never silently publishes a file above the limit.

## Named profiles

Profiles contain all image and output options, including folder recursion. Update
preferences are global. Existing settings keep their values and initially appear
as **Benutzerdefiniert** (custom). The editable starter profiles are:

| Profile | Options |
|---|---|
| Mail | JPEG q85, at most 1600 px and 500,000 bytes; the limit may reduce quality to q15, with no additional resizing |
| Website-Fotos | WebP q75, at most 2000 px, no byte limit |
| Screenshots | Lossless PNG encoding, original dimensions, transparency preserved |

All starters output beside the source and leave recursion off. They never enlarge
images. Mail reports an error without publishing an oversized file if its limit
cannot be met. Lossless refers to encoding after image preparation.

Choose **Verwalten…** in Settings to create a profile from the current form,
rename it, replace its options, delete it or apply it to the form. Library changes
are saved immediately. Applying a profile in Settings only changes the form:
**Speichern** activates it, **Abbrechen** discards the form changes. Creating or
updating a definition never saves other pending form values or update preferences.
The profile name is shown while all options match; changed values show
**Benutzerdefiniert**. A selection from the JD/app/Dock profile menu applies
immediately for future drops. Already queued jobs keep their options and name.

Profiles live in `~/Library/Application Support/JPEGdrop/profiles.json` (schema 1,
stable IDs, up to 100 profiles). Names allow Unicode and punctuation, with 1–64
characters; names differing only by case or Unicode normalization are duplicates.
Reading never creates the library. Starters are stored with the first library
change; deleted starters stay deleted. Corrupt, newer or unknown libraries are
reported and preserved. Concurrent edits are rejected instead of overwriting a
newer definition. Conversion with saved options remains available.

## Optional comparison preview

Choose **Vergleichsvorschau…** from the app, JD or Dock menu and select one image.
Normal drops still convert directly. The preview uses the real encoder with a
complete snapshot of image and output options. **Vorher** shows the oriented,
sRGB source; **Nachher** shows the decoded output, with actual encoded bytes and
pixel dimensions. Choose a profile in the comparison or use **Optionen übernehmen**.
Changes in an open Settings form update the comparison without saving that form
or update preferences. Choosing a profile here affects only this comparison.

**Einpassen** fits both images. **100 % Ausgabe** maps one output pixel to one
screen pixel; when resizing, the source is scaled to the same area. Drag or
scroll to move the shared crop, and use −/+ to zoom. Checkerboard, white, dark and
the selected output background are available for judging transparency.

Only the latest options can be converted with **Mit diesen Optionen konvertieren**.
The resulting job keeps that snapshot, while further drops use saved settings.
A changed source requires recalculation. Until conversion, sources and regular
output directories remain unchanged. One background preview process handles the
latest request; new options discard stale results. **Berechnung abbrechen**, closing
the window and quitting remove its private temporary files. Updates cancel previews
and wait for their processes before relaunch; new previews are blocked meanwhile.
Preview limits are 256 MiB per source, 64 megapixels and two minutes per calculation.
These limits do not change regular conversion. A forced system kill cannot ensure cleanup.

## Finder Quick Action

Choose **Finder-Schnellaktion einrichten…** in the app or JD menu. It requires the
signed, notarized `/Applications/JPEGdrop.app` and installs **Mit JPEGdrop
konvertieren** in `~/Library/Services`. Select files or folders in Finder and use
**Quick Actions** or **Services** from the context menu. If it is absent, enable it
in System Settings → General → Login Items & Extensions → Finder (the wording
depends on macOS). JPEGdrop does not change these system settings.

The action opens the installed app with the selected paths and its saved options;
an unsaved Settings form does not affect it. Spaces, quotes and other filename
characters are passed as arguments. No AppleScript automation permission is
required. Replacing JPEGdrop at the same installation path keeps the action usable.
Moving the app away requires reinstalling it at that path before using the action.
**Finder-Schnellaktion entfernen…** removes only an unchanged JPEGdrop-owned
workflow. A foreign or manually modified workflow is preserved, including added
files; manage that copy yourself in `~/Library/Services`.

## Jobs, progress and cancellation

**Aufträge…** (⌘0) opens the job list with progress, current filenames, results,
warnings and errors. Drops are processed serially, with up to 20 active or
queued jobs. Each job keeps the settings selected when it was submitted.
Unsupported files in a mixed drop are listed as skipped.

**Auftrag abbrechen** cancels a job. Finished copies remain; incomplete temporary
outputs are removed. **Erzeugte Dateien im Finder** selects the existing outputs
of the chosen job, including partial successes. Quitting asks before cancelling
active jobs and waits for their encoder processes to finish. A forced system
kill cannot guarantee cleanup. The list keeps up to 50 completed jobs during
the current session and is cleared on restart. Large inventories evict older
jobs earlier; the latest job remains complete.

## Updates and privacy

**Nach Updates suchen…** checks for signed Sparkle updates. Automatic checks can
be disabled in Settings. Installation requires confirmation and waits for active
conversion work; new drops are blocked while an installation is pending.

Images are processed locally and are never uploaded. Update requests contact
GitHub Pages/Releases; system profile submission is disabled. **Datenschutz…**
in the app menu opens bundled privacy information, available offline.
Read [privacy details](docs/privacy.md), the publisher's
[legal notice](https://dm0.de/impressum.html), or report a problem in
[Issues](https://github.com/danielmuellerir/jpegdrop/issues).

## CLI

From a source checkout with the dependencies installed:

```sh
.venv/bin/python jpegdrop.py --codec avif --preset mittel photo.heic
.venv/bin/python jpegdrop.py --json --codec jpeg photo.png
.venv/bin/python jpegdrop.py --codec webp --preset verlustfrei --preserve-alpha --max-edge 1600 photo.png
.venv/bin/python jpegdrop.py --recursive --output-dir Exports --codec jpeg Album
.venv/bin/python jpegdrop.py --output-subdir JPEGdrop --codec webp photo.png
.venv/bin/python jpegdrop.py --target-size 500KB --target-resize --target-min-edge 320 photo.png
.venv/bin/python jpegdrop.py --list-presets
.venv/bin/python jpegdrop.py --list-profiles --json
.venv/bin/python jpegdrop.py --profile Mail --max-edge 1200 --target-size 0 foto.png
.venv/bin/python jpegdrop.py --install-finder-action
.venv/bin/python jpegdrop.py --remove-finder-action
.venv/bin/python jpegdrop.py --print-settings-path
```

The downloaded app also contains a headless CLI:

```sh
"/Applications/JPEGdrop.app/Contents/Resources/python/bin/python3" -I -B \
  "/Applications/JPEGdrop.app/Contents/Resources/jpegdrop.py" --json photo.heic
```

Without explicit option flags, the CLI uses the saved app settings.
`--profile NAME` uses that profile as the complete image/output basis; explicit
flags override it, regardless of their order. The CLI never saves its selection
or overrides. Profile names match case-insensitively; quote spaces/punctuation
and use `--profile=NAME` if the name starts with a hyphen. `--list-profiles` prints
one name per line, or IDs, names and options with `--json`. An explicit codec
keeps the profile preset if compatible, otherwise uses the codec default. Legacy
`--quality` overrides the profile's codec/preset with JPEG and fixed quality;
contradictory explicit flags are rejected. JPEG removes profile transparency;
explicit `--preserve-alpha` with JPEG is an error.
`--max-edge 0` disables a saved size limit. `--flatten-alpha` overrides saved
alpha preservation, and `--background '#123456'` sets an opaque sRGB background.
`--suffix` changes the default `-klein` suffix for sources already in the target
codec. `-q/--quality` retains fixed-quality JPEG compatibility. The exit status
is the number of errors, capped at 125; zero means success.

`--target-size 0` disables a saved size limit, and `--no-target-resize` disables
additional shrinking. Size suffixes B/KB/MB use 1/1,000/1,000,000 bytes; KiB/MiB
use 1,024/1,048,576 bytes.

`--no-recursive` and `--beside` override saved folder/output choices. For large
file lists, `--input-plan plan.json` accepts a JSON array of objects with absolute
`source` and absolute-or-null `output_directory` paths. It replaces positional
paths, recursion and output flags; image options still apply. Plans are limited
to 10,000 files and 32 MiB.

Versions without the update menu need one manual installation of the current DMG.

## Build and test

Use Python 3.14 and Xcode Command Line Tools on macOS:

```sh
./build.sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/ -q
bash -n build.sh install.sh release.sh release-utils.sh
```

`build.sh` creates a self-contained bundle in the checkout. It attempts Developer
ID signing and notarization when configured, otherwise producing a development
build. `install.sh` requires a valid notary ticket before replacing an installation.
`release.sh` requires a clean source commit and produces a signed, notarized DMG
and SHA256 file without installing the app. See [release instructions](docs/release.md).

JPEGdrop's original code is [MIT licensed](LICENSE). Bundled dependencies keep
[their own licenses](licenses/README.md); notices are included in the app and DMG.
