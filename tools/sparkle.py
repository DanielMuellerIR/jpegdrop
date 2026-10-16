#!/usr/bin/env python3
"""Exakt gepinntes Sparkle-Paket prüfen und in ein neues Bundle einbetten."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

VERSION = "2.10.0"
ARCHIVE_NAME = "Sparkle-for-Swift-Package-Manager.zip"
ARCHIVE_SHA256 = "17e28312b8e18ab7cdbbe09a6fb28cc55a5479ec6c371dbc07cdecd2a14fd959"
ARCHIVE_URL = f"https://github.com/sparkle-project/Sparkle/releases/download/{VERSION}/{ARCHIVE_NAME}"
FRAMEWORK_PATH = "Sparkle.xcframework/macos-arm64_x86_64/Sparkle.framework"


def validate_test_feed(url: str) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.username or parsed.password or parsed.fragment
            or not parsed.path.endswith(".xml")):
        raise ValueError("Ein Testfeed muss eine lokale HTTP-XML-URL ohne Zugangsdaten sein.")
    _ = parsed.port
    return url


def verified_archive(cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / ARCHIVE_NAME
    if archive.is_file() and hashlib.sha256(archive.read_bytes()).hexdigest() == ARCHIVE_SHA256:
        return archive
    with tempfile.NamedTemporaryFile(prefix="download-", dir=cache, delete=False) as target:
        temporary = Path(target.name)
        try:
            with urlopen(Request(ARCHIVE_URL, headers={"User-Agent": "JPEGdrop-build"}), timeout=60) as source:
                shutil.copyfileobj(source, target)
            target.flush()
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != ARCHIVE_SHA256:
                raise ValueError("Sparkle-Archiv hat eine unerwartete SHA256-Prüfsumme.")
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)
    return archive


def embed(app: Path, cache: Path, test_feed: str | None = None) -> None:
    if test_feed is not None:
        validate_test_feed(test_feed)
    archive = verified_archive(cache)
    with tempfile.TemporaryDirectory(prefix="extract-", dir=cache) as directory:
        package = Path(directory)
        subprocess.run(["/usr/bin/ditto", "-x", "-k", str(archive), str(package)], check=True)
        framework = package / FRAMEWORK_PATH
        with (framework / "Resources/Info.plist").open("rb") as source:
            info = plistlib.load(source)
        if info["CFBundleShortVersionString"] != VERSION:
            raise ValueError("Sparkle-Version im Framework stimmt nicht mit dem Pin überein.")
        target = app / "Contents/Frameworks/Sparkle.framework"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(framework, target, symlinks=True)
        # JPEGdrop läuft ohne App-Sandbox. Sparkle empfiehlt, die dafür
        # unbenötigten XPC-Dienste aus dieser Distribution zu entfernen.
        shutil.rmtree(target / "Versions/B/XPCServices")
        (target / "XPCServices").unlink(missing_ok=True)
        licenses = app / "Contents/Resources/Licenses"
        licenses.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(package / "LICENSE", licenses / "Sparkle-LICENSE.txt")
    if test_feed:
        path = app / "Contents/Info.plist"
        with path.open("rb") as source:
            info = plistlib.load(source)
        info.update(SUFeedURL=test_feed, SUEnableAutomaticChecks=False, JPEGdropTestUpdateFeed=True)
        info["NSAppTransportSecurity"] = {"NSAllowsLocalNetworking": True}
        with path.open("wb") as target:
            plistlib.dump(info, target, sort_keys=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--test-feed-url")
    args = parser.parse_args()
    embed(args.app, args.cache, args.test_feed_url)
    print(f"Sparkle {VERSION}: Archiv geprüft, Framework und Lizenz eingebettet.")


if __name__ == "__main__":
    main()
