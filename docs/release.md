# Release procedure

Build on Apple Silicon with Python 3.14 and Xcode Command Line Tools. The app
contains its own CPython runtime, Pillow, pi-heif, PyObjC and Sparkle. It does
not rely on a user's virtual environment or Homebrew installation.

## Signing configuration

A Developer ID Application certificate and a working notarytool keychain profile
are required. Signing identity is selected from `CODESIGN_IDENTITY`, then
`git config jpegdrop.codesignIdentity`, otherwise the only available Developer ID
certificate. Ambiguous identities are rejected. An exact certificate hash can
be configured when multiple certificates have the same name.

The notary profile comes from `NOTARY_PROFILE`, then
`git config jpegdrop.notaryProfile`, with default `jpegdrop-notary`. Configure
credentials using `xcrun notarytool store-credentials`; keep credentials in the
keychain, never in source files, environment dumps or command transcripts.

## Build and validate

1. Update the version in `jpegdrop.py`, README information and changelog as needed.
2. Run the tests and shell syntax checks. Commit the release source; the working
   tree must be clean throughout the build.
3. Run `./release.sh`. For a plain DMG, use `./release.sh --no-finder-layout`.
4. Verify `dist/JPEGdrop-<version>.dmg.sha256` with `shasum -a 256 -c` from `dist`.
5. Create corresponding dependency sources:

   ```sh
   .venv/bin/python tools/package_sources.py \
     --output dist/JPEGdrop-<version>-dependency-sources.tar.gz
   ```

6. Test the actual DMG on another Mac, inspect its Finder layout, launch the
   copied app outside the source checkout and perform a real signed update.

The release script notarizes both app and DMG, then mounts the finished image
read-only and checks the contained app's deep signature, ticket, Gatekeeper
acceptance, bundle identifier, both versions, source manifest and signature hash.
It detaches only its own device. Existing artifacts are never overwritten.
A failed release leaves the installed app untouched; `release.sh` never installs.
Use `./install.sh` separately for a verified installation.

`BuildInfo.json` records the clean source commit/tree, Python pin and requirements
checksum. Every native arm64 file is checked against the declared macOS minimum.
This checks binary deployment targets; it does not replace execution on older
macOS versions. Record the actual tested systems in release notes.

## Sparkle publication

The notarized DMG is also the Sparkle update archive. The appcast workflow
verifies its payload against the release tag before signing. It verifies both
the archive and feed signatures, then deploys the exact XML bytes to Pages.
A failed deployment should be restarted with a fresh workflow dispatch for
the tag, rather than rerunning failed jobs in the same attempt.


The production feed is `https://danielmuellerir.github.io/jpegdrop/appcast.xml`.
Only the public Ed25519 key is in the app. Signing tools obtain the private key
from the configured local keychain account. Both archive and XML feed must be
signed and independently verified before publication.

Publish the DMG, its checksum and the dependency source archive
under the tag matching the build's source commit. Upload the artifacts before
publishing a feed that references them. Serve the signed XML without rewriting
its bytes. Test the published URLs, download hashes and signatures, then update
an older notarized installation through the public feed and confirm relaunch,
settings preservation and signature validity after use.

A loopback feed can be selected for an isolated development build with
`JPEGDROP_TEST_FEED_URL`. It disables automatic checks. Release scripts reject
that marker, environment variable and non-HTTPS production feeds. Such builds
must never be uploaded as releases.

The CI workflow runs conversion, lifecycle, native control and release-guard
tests plus shell syntax checks. Pull-request jobs receive no signing or notary
credentials. Actual signing, notarization and GUI acceptance remain release checks.
