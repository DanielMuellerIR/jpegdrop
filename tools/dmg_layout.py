#!/usr/bin/env python3
"""Finder-Layout direkt in das eigene eingehängte Release-Image schreiben."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil

from ds_store import DSStore
from mac_alias import Alias


def write_layout(root: Path, background: Path) -> None:
    root = root.resolve(strict=True)
    if not os.path.ismount(root) or not (root / "JPEGdrop.app").is_dir():
        raise ValueError("Das Layout benötigt das eingehängte JPEGdrop-Release-Image.")
    if (root / ".DS_Store").exists() or (root / ".background").exists():
        raise ValueError("Das Image enthält bereits Layout-Dateien; sie werden nicht überschrieben.")
    target = root / ".background/background.tiff"
    target.parent.mkdir()
    shutil.copyfile(background, target)
    alias = Alias.for_file(str(target))
    if alias.volume.name != b"JPEGdrop":
        raise ValueError("Unerwarteter Volume-Name für das JPEGdrop-Layout.")
    # Die Alias-Datei identifiziert Volume und Ziel über deren IDs. Der
    # zusätzliche POSIX-Fallback darf keinen privaten Build-Mountpunkt verraten.
    alias.volume.posix_path = b"/Volumes/JPEGdrop"
    data = alias.to_bytes()
    for private in (str(root), str(Path.home())):
        if any(private.encode(encoding) in data for encoding in ("utf-8", "utf-16-be", "utf-16-le")):
            raise ValueError("Hintergrund-Alias enthält einen privaten Pfad.")
    readme = "Read Me.txt" if (root / "Read Me.txt").is_file() else "LIESMICH.txt"
    locations = {"JPEGdrop.app": (180, 195), "Applications": (500, 195)}
    if (root / "Licenses").is_dir():
        locations.update({readme: (280, 375), "Licenses": (400, 375)})
    else:
        locations[readme] = (340, 375)
    with DSStore.open(str(root / ".DS_Store"), "w+") as store:
        store["."]["vSrn"] = ("long", 1)
        store["."]["icvl"] = ("type", b"icnv")
        store["."]["bwsp"] = {"WindowBounds": "{{180, 140}, {680, 520}}", "ShowToolbar": False,
            "ShowStatusBar": False, "ShowPathbar": False, "ShowSidebar": False, "ShowTabView": False,
            "ContainerShowSidebar": False, "PreviewPaneVisibility": False}
        store["."]["icvp"] = {"viewOptionsVersion": 1, "backgroundType": 2,
            "backgroundColorRed": 1.0, "backgroundColorGreen": 1.0, "backgroundColorBlue": 1.0,
            "backgroundImageAlias": data, "gridOffsetX": 0.0, "gridOffsetY": 0.0,
            "gridSpacing": 100.0, "arrangeBy": "none", "showIconPreview": True,
            "showItemInfo": False, "labelOnBottom": True, "textSize": 14.0, "iconSize": 96.0,
            "scrollPositionX": 0.0, "scrollPositionY": 0.0}
        for name, point in locations.items():
            store[name]["Iloc"] = point


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mountpoint", required=True, type=Path)
    parser.add_argument("--background", required=True, type=Path)
    args = parser.parse_args()
    write_layout(args.mountpoint, args.background)
