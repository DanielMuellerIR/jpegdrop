# Third-party components

JPEGdrop's original code is available under the MIT license in `LICENSE`.
Bundled components retain their own licenses. `Contents/Resources/Licenses`
in the app, and the `Licenses` folder in the DMG, contain these notices.
The build writes `runtime-inventory.json` from the installed packages.

| Component | Version / source | License material |
|---|---|---|
| CPython | 3.14.6, python-build-standalone 20260728 | `CPython/`, PSF and component notices |
| Pillow and its bundled image libraries | 12.2.0 | Complete wheel `Pillow-LICENSE` |
| pi-heif Python wrapper | 1.4.0 | BSD 3-Clause, wheel notices |
| libheif | 1.23.0, dynamically linked | LGPL 3, `libheif-COPYING.txt` |
| libde265 | 1.1.0, dynamically linked | LGPL 3, `LGPL-3.0.txt`, authors |
| PyObjC Core and Cocoa bindings | 12.2 | MIT, both upstream notices |
| Sparkle | 2.10.0 | MIT and bundled notices, `Sparkle-LICENSE.txt` |

The HEIF wheel's upstream bundled-license notice contains older library
version references. The versions above come from the loaded libraries and
are checked during the build. JPEGdrop uses the decoder-only pi-heif wheel;
it does not bundle an x265 HEIC encoder. Pillow provides the output codecs.

The CPython notices were taken from the matching full distribution archive,
whose checksum is recorded in `provenance.json`. Its metadata identifies
bzip2, libffi, mpdecimal, expat, OpenSSL, liblzma, libuuid and zstd among the
linked components. Some upstream license texts also cover components removed
when the runtime is trimmed. macOS system libraries remain part of macOS.
The zstd 1.5.7 BSD notice is supplied separately because the full archive
references it without including the text.

## HEIF library sources and modification

`sources.json` records the exact upstream source archives and SHA256 values.
Each public release also provides a dependency source archive containing
pi-heif, libheif, libde265 and the upstream build recipe. These components
are distributed without source modifications by JPEGdrop.

You may modify the LGPL libraries, replace them with compatible builds and
debug those modifications. See [the rebuilding instructions](rebuilding-heif.md).
The GNU GPL 3 text is included because LGPL 3 incorporates its terms.
No additional restriction on reverse engineering for debugging modifications
to these libraries is imposed by JPEGdrop.

The ds_store and mac_alias packages are build tools only. They are not included
in the distributed app. Their installed package notices remain with the tools.
