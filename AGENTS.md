# JPEGdrop development

JPEGdrop is a macOS dock droplet with a headless Python conversion core.
Images are written as metadata-free copies beside their sources. Source files
must never be modified or overwritten, and output names must handle collisions.

## Architecture

- `jpegdrop.py`: Pillow/pi-heif conversion, presets, settings and CLI.
- `app/menu_app.py`: thin PyObjC/Cocoa interface and Finder drop handling.
- `app/jobs.py`: serial background jobs, progress, cancellation and shutdown.
- `app/updates.py`: Sparkle integration; active jobs delay installation.
- `app/launcher.m`: native entry point embedding CPython so the main bundle
  remains JPEGdrop during Sparkle updates.
- `build.sh`: self-contained arm64 bundle, dependencies, licenses and signing.
- `install.sh`: verified notarized installation into `/Applications`.
- `release.sh`: notarized DMG, Finder layout and final payload verification.

## Invariants

- Apply EXIF orientation and bake supported color profiles into sRGB before
  stripping metadata. Invalid profiles and multiple frames produce clear errors.
- Keep conversion logic in the CLI core; the interface does not implement it again.
- Do not write bytecode or other files into the signed bundle at runtime.
- Bundle paths must be relative to the bundle, without build-machine paths.
- Updates stop new submissions and wait for queued/running work before relaunch.
- Build output stays outside `/Applications` and `/System`. Only the installer
  may replace an installation after signature, notary and Gatekeeper checks.
- Release artifacts must come from a clean, unchanged commit. Existing artifacts
  are never overwritten. Mount cleanup may only detach the device owned by its run.
- Test feeds are restricted to loopback addresses and cannot be released.

## Development

Use English identifiers. Comments and existing interface text are German.
Keep English and German READMEs synchronized. Preserve upstream license notices.
`.venv`, `.cache`, bundles and `dist` are local artifacts and must not be committed.

Install `requirements-dev.txt`, then run `.venv/bin/python -m pytest tests/ -q`.
Check shell syntax with `bash -n build.sh install.sh release.sh release-utils.sh`.
Native UI, signing and real update behavior also require a macOS run; mock tests
alone cannot establish those properties. See `docs/release.md` for distribution.
