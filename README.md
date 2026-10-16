# JPEGdrop

**🌐 Sprache / Language:** [English](README.md) · [Deutsch](README.de.md)

JPEGdrop converts images dropped onto its macOS Dock icon into smaller, metadata-free JPEG, PNG, AVIF or WebP copies beside the originals.

## Install and use

1. Download the DMG from [Releases](https://github.com/danielmuellerir/jpegdrop/releases).
2. Drag JPEGdrop to Applications and open it. Python and Homebrew are not required.
3. Choose a codec and preset from the **JD** menu or **Einstellungen…** (⌘,).
4. Drop images onto the app's Dock or Finder icon.

The interface currently uses German labels. The default is **JPEG · hoch**
(quality 85). JPEGdrop builds for Apple Silicon only. The bundle declares macOS
12.0 as its minimum; release tests have run on macOS 26.7.1. Execution on macOS
12 has not yet been verified. See the release notes for the tested systems.

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
- Transparency is currently flattened onto white for every output codec.
  A warning identifies images whose transparent pixels were affected.
- Animated or multi-image files are rejected. The first frame is not silently
  exported as a still image.
- Filenames, filesystem timestamps and visible image contents are not anonymized.

JPEG, AVIF and WebP offer six fixed quality levels and **auto**, which adapts
quality to the image's longer edge. It does not resize the image.
PNG offers lossless encoding or a reduced 256-color palette. “Lossless” describes
PNG encoding after RGB/sRGB conversion and transparency flattening; those
preparation steps may change source pixels. The highest JPEG preset still uses
lossy encoding. An already optimized image may become larger; sizes and warnings
are reported honestly.

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
the current session and is cleared on restart.

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
.venv/bin/python jpegdrop.py --list-presets
.venv/bin/python jpegdrop.py --print-settings-path
```

The downloaded app also contains a headless CLI:

```sh
"/Applications/JPEGdrop.app/Contents/Resources/python/bin/python3" -B \
  "/Applications/JPEGdrop.app/Contents/Resources/jpegdrop.py" --json photo.heic
```

Without explicit codec/preset flags, the CLI uses the saved app settings.
`--suffix` changes the default `-klein` suffix for sources already in the target
codec. `-q/--quality` retains fixed-quality JPEG compatibility. The exit status
is the number of errors, capped at 125; zero means success.

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
