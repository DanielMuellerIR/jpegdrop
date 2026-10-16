#!/usr/bin/env python3
"""Jede native Laufzeitdatei auf arm64 und die deklarierte macOS-Untergrenze prüfen."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import plistlib
import re
import subprocess

MACH_HEADERS = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"}


def version_tuple(value: str) -> tuple:
    pieces = tuple(int(part) for part in value.split("."))
    return pieces + (0,) * max(0, 3 - len(pieces))


def inspect_app(app: Path) -> dict:
    with (app / "Contents/Info.plist").open("rb") as source:
        declared = plistlib.load(source)["LSMinimumSystemVersion"]
    files = []
    for path in app.rglob("*"):
        if not path.is_file() or path.is_symlink() or any(part.endswith(".dSYM") for part in path.parts):
            continue
        with path.open("rb") as source:
            if source.read(4) not in MACH_HEADERS:
                continue
        result = subprocess.run(["/usr/bin/xcrun", "vtool", "-arch", "arm64", "-show-build", str(path)],
                                capture_output=True, text=True)
        minimums = re.findall(r"^\s*minos\s+([\d.]+)\s*$", result.stdout, re.MULTILINE)
        if result.returncode or not minimums or not re.search(r"platform\s+MACOS\b", result.stdout):
            raise ValueError(f"Keine prüfbare macOS-arm64-Laufzeit: {path.relative_to(app)}")
        minimum = max(minimums, key=version_tuple)
        if version_tuple(minimum) > version_tuple(declared):
            raise ValueError(f"{path.relative_to(app)} benötigt macOS {minimum}; deklariert ist {declared}.")
        files.append({"path": str(path.relative_to(app)), "minimumSystemVersion": minimum})
    if not files:
        raise ValueError("Das Bundle enthält keine prüfbare native Laufzeit.")
    return {"architecture": "arm64", "declaredMinimumSystemVersion": declared,
            "highestMinimumSystemVersion": max((row["minimumSystemVersion"] for row in files), key=version_tuple),
            "nativeFiles": len(files)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(inspect_app(args.app), ensure_ascii=False))
    except (ValueError, OSError, KeyError) as error:
        parser.exit(2, f"FEHLER: {error}\n")


if __name__ == "__main__":
    main()
