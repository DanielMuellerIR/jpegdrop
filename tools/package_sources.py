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


def verify_package(repo: Path, archive: Path) -> None:
    sources = json.loads((repo / "licenses/sources.json").read_text())
    with tarfile.open(archive) as tar:
        def content(name):
            member = tar.getmember(name)
            if not member.isfile() or member.size > 100_000_000:
                raise ValueError("Unerwarteter Eintrag im Quellenpaket.")
            return tar.extractfile(member).read()
        for source in sources:
            name = f'{source["name"]}-{source["version"]}.tar.gz'
            if hashlib.sha256(content(name)).hexdigest() != source["sha256"]:
                raise ValueError(f"Quellpaket enthält abweichende Bibliothek: {name}")
        for notice in (repo / "licenses").rglob("*"):
            if notice.is_file() and content("Licenses/" + notice.relative_to(repo / "licenses").as_posix()) != notice.read_bytes():
                raise ValueError("Lizenzhinweise im Quellenpaket weichen vom Quellstand ab.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--output", type=Path)
    actions.add_argument("--verify", type=Path)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        if args.verify:
            verify_package(args.repo, args.verify)
        else:
            digest = package_sources(args.repo, args.output)
    except (OSError, ValueError, KeyError, tarfile.TarError) as error:
        parser.exit(2, f"FEHLER: {error}\n")
    print("Quellenpaket und Lizenzhinweise geprüft." if args.verify else f"{digest}  {args.output.name}")


if __name__ == "__main__":
    main()
