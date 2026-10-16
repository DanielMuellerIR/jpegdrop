#!/usr/bin/env python3
"""Geprüfte HEIF-Quellarchive für die Weitergabe mit einem Release zusammenstellen."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tarfile
import tempfile
import urllib.request
import os


def package_sources(repo: Path, output: Path) -> str:
    if output.exists() or output.is_symlink():
        raise ValueError("Das Quellpaket-Ziel existiert bereits und wird nicht überschrieben.")
    output.parent.mkdir(parents=True, exist_ok=True)
    sources = json.loads((repo / "licenses/sources.json").read_text())
    with tempfile.TemporaryDirectory(prefix=".jpegdrop-sources-", dir=output.parent) as directory:
        work = Path(directory)
        paths = []
        for source in sources:
            if not source["url"].startswith("https://"):
                raise ValueError("Quellarchive benötigen HTTPS.")
            path = work / f'{source["name"]}-{source["version"]}.tar.gz'
            with urllib.request.urlopen(source["url"], timeout=60) as response, path.open("wb") as target:
                import shutil
                shutil.copyfileobj(response, target)
            if hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]:
                raise ValueError(f'Prüfsumme des Quellarchivs {path.name} stimmt nicht.')
            paths.append(path)
        checksum = work / "SHA256SUMS"
        checksum.write_text("".join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in paths))
        archive = work / "sources.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            for path in [*paths, checksum]:
                tar.add(path, arcname=path.name)
            tar.add(repo / "licenses", arcname="Licenses")
        # Gleicher Dateisystembereich, kein Überschreiben bei einer Kollision.
        os.link(archive, output, follow_symlinks=False)
    return hashlib.sha256(output.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        digest = package_sources(args.repo, args.output)
    except (OSError, ValueError) as error:
        parser.exit(2, f"FEHLER: {error}\n")
    print(f"{digest}  {args.output.name}")


if __name__ == "__main__":
    main()
