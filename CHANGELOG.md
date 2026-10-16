# Changelog

## 2.12.0 — 2026-10-16

- Public release of named profiles, the Finder Quick Action and the optional comparison preview.
- Profiles, size limits and output locations work together through complete job snapshots.
- The declared macOS 12 minimum remains unverified at runtime; tested systems are listed in the release notes.

## 2.11.0 — 2026-10-16

- Optional native before/after comparison using the real encoder and complete option snapshots.
- Actual encoded sizes and dimensions, shared crop and zoom, output-pixel view and transparency backgrounds.
- Bounded background previews with stale-result rejection, cancellation and temporary-file cleanup.
- Conversion with reviewed options, unchanged unsaved Settings forms and preview-aware update/shutdown protection.

## 2.10.0 — 2026-10-16

- Named profiles with editable Mail, Website-Fotos and Screenshots starters.
- Native profile management, saved/custom indicators and unchanged queued-job snapshots.
- CLI profile selection with explicit-option priority and JSON profile inventory.
- Finder Quick Action for the notarized installed app, with safe path arguments and protection for modified workflows.
- Profile writes reject stale editors and preserve corrupt or unsupported libraries.

## 2.9.0 — 2026-10-16

- Optional per-file byte limits with bounded quality search and visible progress.
- Explicit additional resizing with a minimum image edge; lossless presets retain their encoding.
- Unreachable limits produce a clear error and no oversized output.
- Complete size-option snapshots, unambiguous decimal/binary units and cancellation cleanup.

## 2.8.0 — 2026-10-16

- Folder drops with optional recursion and a fixed, bounded input inventory.
- Output beside sources, in a named subfolder or in a chosen directory while preserving structure.
- Duplicate/symlink handling and exclusion of the selected output area from scanning.
- Large jobs use bounded input manifests and retain source/output mappings through cancellation.
- Native destination chooser and visible inventory progress.

## 2.7.0 — 2026-10-16

- Proportional maximum-edge resizing without upscaling, after EXIF orientation.
- Optional PNG, AVIF and WebP transparency; configurable opaque sRGB background.
- True lossless WebP, including prepared pixels in fully transparent areas.
- Complete immutable image-option snapshots for queued jobs.
- Source/output dimensions and alpha handling in CLI and job results.
- Native image settings with input validation and compatible defaults.

## 2.6.3 — 2026-10-16

- Installation waits for native JPEGdrop processes before exchanging the app.
- Finder DMG background, icon spacing and document labels render correctly.

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
