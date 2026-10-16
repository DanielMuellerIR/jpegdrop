#!/usr/bin/env python3
"""Lizenztexte des tatsächlichen Laufzeitbestands vor dem Signieren beilegen."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
import shutil
import ssl
import sys


def collect(repo: Path, app: Path) -> dict:
    import pi_heif
    from PIL import features

    expected = {"Pillow": "12.2.0", "pi-heif": "1.4.0", "pyobjc-core": "12.2",
                "pyobjc-framework-Cocoa": "12.2"}
    versions = {name: importlib.metadata.version(name) for name in expected}
    if versions != expected:
        raise ValueError("Lizenzinventur passt nicht zu den installierten Paketversionen.")
    native = pi_heif.libheif_info()
    if native["libheif"] != "1.23.0" or "version 1.1.0" not in native["decoders"].get("libde265", ""):
        raise ValueError("HEIF-Laufzeit weicht vom geprüften Lizenz-/Quellbestand ab.")
    if native.get("HEIF") or native.get("AVIF"):
        raise ValueError("Unerwarteter HEIF-/AVIF-Encoder im Decoder-Wheel.")
    target = app / "Contents/Resources/Licenses"
    shutil.copytree(repo / "licenses", target, dirs_exist_ok=True)
    shutil.copyfile(repo / "LICENSE", target / "JPEGdrop-MIT.txt")
    for package, prefix in [("Pillow", "Pillow"), ("pi-heif", "pi-heif")]:
        distribution = importlib.metadata.distribution(package)
        files = [f for f in distribution.files or [] if "/licenses/" in str(f)]
        if not files:
            raise ValueError(f"Lizenzdateien fehlen im installierten {package}-Wheel.")
        for file in files:
            source = Path(distribution.locate_file(file))
            shutil.copyfile(source, target / (prefix + "-" + source.name))
    inventory = {"packages": versions, "python": sys.version.split()[0],
                 "openssl": ssl.OPENSSL_VERSION, "heif": native,
                 "pillowFeatures": {name: features.version(name) for name in features.get_supported()},
                 "sourceArchives": json.loads((repo / "licenses/sources.json").read_text())}
    (target / "runtime-inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")
    return inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--app", required=True, type=Path)
    args = parser.parse_args()
    try:
        inventory = collect(args.repo, args.app)
    except (OSError, ValueError, importlib.metadata.PackageNotFoundError) as error:
        parser.exit(2, f"FEHLER: {error}\n")
    print("Lizenzbestand eingebettet: " + ", ".join(inventory["packages"]))


if __name__ == "__main__":
    main()
