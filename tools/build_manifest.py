#!/usr/bin/env python3
"""Quellcommit im Bundle festhalten und Releases an einen sauberen Stand binden."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import subprocess


def source_state(repo: Path) -> dict:
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL,
                                       text=True).strip()
    try:
        return {"commit": git("rev-parse", "HEAD"), "tree": git("rev-parse", "HEAD^{tree}"),
                "clean": not git("status", "--porcelain", "--untracked-files=normal")}
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "tree": None, "clean": False}


def require_clean_source(repo: Path) -> dict:
    state = source_state(repo)
    if not state["commit"] or not state["clean"]:
        raise ValueError("Ein Release benötigt einen sauberen, committeten Git-Quellstand.")
    return state


def write_manifest(repo: Path, app: Path, python_version: str, python_release: str) -> None:
    with (app / "Contents/Info.plist").open("rb") as source:
        info = plistlib.load(source)
    manifest = {
        "schemaVersion": 1, "version": info["CFBundleShortVersionString"],
        "source": source_state(repo),
        "requirementsSHA256": hashlib.sha256((repo / "requirements.txt").read_bytes()).hexdigest(),
        "python": {"version": python_version, "standaloneRelease": python_release},
        "architecture": "arm64", "minimumSystemVersion": info["LSMinimumSystemVersion"],
    }
    (app / "Contents/Resources/BuildInfo.json").write_text(json.dumps(manifest, indent=2) + "\n")


def verify_release_source(repo: Path, app: Path, version: str) -> dict:
    state = require_clean_source(repo)
    manifest = json.loads((app / "Contents/Resources/BuildInfo.json").read_text())
    with (app / "Contents/Info.plist").open("rb") as source:
        info = plistlib.load(source)
    if (manifest.get("schemaVersion") != 1 or manifest.get("source") != state
            or manifest.get("version") != version
            or info.get("CFBundleShortVersionString") != version or info.get("CFBundleVersion") != version
            or info.get("CFBundleIdentifier") != "de.dm0.jpegdrop"
            or manifest.get("requirementsSHA256") != hashlib.sha256((repo / "requirements.txt").read_bytes()).hexdigest()):
        raise ValueError("App, Version und Quellcommit stimmen nicht mit dem aktuellen Release-Stand überein.")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--check-source", action="store_true")
    actions.add_argument("--write", action="store_true")
    actions.add_argument("--verify", action="store_true")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--app", type=Path)
    parser.add_argument("--version")
    parser.add_argument("--python-version")
    parser.add_argument("--python-release")
    args = parser.parse_args()
    try:
        if args.check_source:
            print(require_clean_source(args.repo)["commit"])
        elif args.write and args.app and args.python_version and args.python_release:
            write_manifest(args.repo, args.app, args.python_version, args.python_release)
        elif args.verify and args.app and args.version:
            print(verify_release_source(args.repo, args.app, args.version)["source"]["commit"])
        else:
            parser.error("Für Schreiben/Prüfen fehlen App-, Versions- oder Interpreterangaben.")
    except (ValueError, OSError, KeyError) as error:
        parser.exit(2, f"FEHLER: {error}\n")


if __name__ == "__main__":
    main()
