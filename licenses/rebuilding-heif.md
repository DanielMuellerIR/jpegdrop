# Replacing the HEIF libraries

JPEGdrop loads libheif and libde265 dynamically through pi-heif. Their sources
and the pi-heif build recipe are provided in each release's dependency source
archive. Exact upstream versions and hashes are listed in `licenses/sources.json`.

For a normal source build, install Python 3.14, Xcode Command Line Tools and
run `./build.sh`. The default build installs the pinned, unmodified pi-heif wheel.

To use a modified library, work on a copy of the app outside `/Applications`.
Build a compatible arm64 libde265 1.1.0 and libheif 1.23.0 from the supplied
sources with your changes. The pi-heif source archive's `libheif/build_libs.py`
records the upstream build options; `PH_LIGHT_ACTION=1` selects the decoder-only
configuration. Use your own build/install directory rather than the script's
system installation defaults. Preserve the dylib install names and their
bundle-relative loader paths when replacing files under:

```
JPEGdrop.app/Contents/Resources/python/lib/python3.14/site-packages/pi_heif/.dylibs/
```

The distributed library filenames are `libheif.1.23.0.dylib` and
`libde265.0.2.0.dylib` (the latter is the ABI filename of libde265 1.1.0).
`otool -L` shows their existing loader paths. Keep those paths or adjust them
with `install_name_tool` to point to the replacements in the same folder.

Modifying a signed app invalidates its original signature. You do not need the
publisher's signing key to run a modified copy. Remove its old signature and
sign your copy ad hoc with library validation disabled for the native launcher
and CLI interpreter. The existing `alles_signieren` function in `build.sh`
implements this path when no Developer ID is used; adapt the normal source
build to your modified libraries before that signing step. Keep modified builds
in your development directory. Do not install them with `install.sh`, which
requires notarization.

You can instead run the Python core directly in a virtual environment with a
pi-heif build linked to your modified libraries. This permits inspecting and
debugging the library behavior without signing a GUI bundle. Redistribution
of modified components must retain the relevant notices and license terms.
