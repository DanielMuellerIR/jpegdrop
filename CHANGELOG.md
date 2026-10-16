# Changelog

## 2.6.2 — 2026-10-16

- MIT license for JPEGdrop, bundled third-party notices and corresponding HEIF sources.
- Decoder-only HEIC dependency; output behavior remains unchanged.
- English and German documentation, offline privacy information and project links.
- macOS test workflow without signing credentials.
- First public distribution through GitHub Releases and a signed Sparkle feed.
- Conversion subprocesses use the bundled Python packages independently of external Python paths.

## 2.5.1 — 2026-10-16

- Signed Sparkle updates with optional automatic checks and confirmed installation.
- Native launcher preserves JPEGdrop's bundle identity for update relaunches.
- Active conversion work delays installation and temporarily blocks new drops.
- Notarized DMG with Retina Finder background and verification of its final app payload.
- Clean source manifest, native deployment-target checks and safer artifact publication.

## 2.4.0 — 2026-10-16

- Serial conversion jobs, queue, progress and per-file results.
- Cancellation and orderly shutdown of conversion processes.
- Finder selection for finished outputs, including partial successes.

## 2.3.0 — 2026-10-16

- Explicit rejection of animated and multi-image inputs.
- Clearer color-profile errors, transparency warnings and quality descriptions.
- Safer output publication and handling of unusual filenames.
