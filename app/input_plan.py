"""Begrenzte Eingabeinventur und Zielzuordnung, ohne Bild- oder AppKit-Abhängigkeit."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
from typing import Callable

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff",
                              ".webp", ".avif", ".gif", ".bmp", ".jp2"})
PACKAGE_EXTENSIONS = frozenset({".app", ".bundle", ".framework", ".photoslibrary", ".photolibrary"})
MAX_IMAGES = 10_000
MAX_ENTRIES = 50_000
MAX_ISSUES = 1_000


@dataclass(frozen=True)
class InventoryOptions:
    recursive: bool = False
    output_mode: str = "beside"
    output_directory: str = ""
    output_subdir: str = "JPEGdrop"

    def __post_init__(self):
        if type(self.recursive) is not bool:
            raise ValueError("Unterordner einbeziehen muss ein Wahrheitswert sein.")
        if not isinstance(self.output_mode, str) or self.output_mode not in {"beside", "directory", "subfolder"}:
            raise ValueError("Unbekannte Zielablage.")
        if not isinstance(self.output_directory, str) or "\0" in self.output_directory:
            raise ValueError("Ungültiger Zielordner.")
        if self.output_directory and not Path(self.output_directory).is_absolute():
            raise ValueError("Der Zielordner muss ein absoluter Pfad sein.")
        if self.output_mode == "directory" and not self.output_directory:
            raise ValueError("Bitte einen Zielordner wählen.")
        if (not isinstance(self.output_subdir, str) or not self.output_subdir.strip()
                or self.output_subdir in {".", ".."}
                or any(c in self.output_subdir for c in ("/", "\\", "\0"))):
            raise ValueError("Der Ausgabe-Unterordner muss ein einzelner Verzeichnisname sein.")

    @classmethod
    def from_settings(cls, settings: dict) -> InventoryOptions:
        return cls(**{key: settings.get(key, value) for key, value in
                      {"recursive": False, "output_mode": "beside", "output_directory": "",
                       "output_subdir": "JPEGdrop"}.items()})


@dataclass(frozen=True)
class InputEntry:
    source: str
    output_directory: str | None = None


@dataclass(frozen=True)
class InputIssue:
    source: str
    message: str
    error: bool = False


@dataclass(frozen=True)
class InputInventory:
    entries: tuple[InputEntry, ...]
    issues: tuple[InputIssue, ...]


class InventoryCancelled(Exception):
    pass


def inventory_inputs(paths: list[str] | tuple[str, ...], options: InventoryOptions,
                     *, cancelled: Callable[[], bool] = lambda: False,
                     max_images: int = MAX_IMAGES, max_entries: int = MAX_ENTRIES) -> InputInventory:
    entries: list[InputEntry] = []
    issues: list[InputIssue] = []
    seen: set[tuple[int, int]] = set()
    seen_directories: set[tuple[int, int]] = set()
    examined = 0
    omitted = 0

    def issue(path: Path, message: str, *, error: bool = False):
        nonlocal omitted
        if len(issues) < MAX_ISSUES:
            issues.append(InputIssue(str(path), message, error))
        elif error:
            raise ValueError("Zu viele Inventurfehler; vor der Konvertierung abgebrochen.")
        else:
            omitted += 1

    def check():
        nonlocal examined
        if cancelled():
            raise InventoryCancelled()
        examined += 1
        if examined > max_entries:
            raise ValueError(f"Die Inventur überschreitet {max_entries} Einträge; keine Bilder wurden konvertiert.")

    def destination(path: Path, root: Path | None) -> str | None:
        if options.output_mode == "beside":
            return None
        if options.output_mode == "subfolder":
            base = (root or path.parent) / options.output_subdir
        else:
            base = Path(options.output_directory)
            if root is not None:
                base /= root.name or "Bilder"
        return str(base / path.relative_to(root).parent) if root is not None else str(base)

    def add(path: Path, root: Path | None, metadata: os.stat_result | None):
        if metadata is not None:
            identity = (metadata.st_dev, metadata.st_ino)
            if identity in seen:
                issue(path, "Doppelte Eingabe übersprungen.")
                return
            seen.add(identity)
        if len(entries) >= max_images:
            raise ValueError(f"Die Inventur überschreitet {max_images} Bilder; keine Bilder wurden konvertiert.")
        entries.append(InputEntry(str(path), destination(path, root)))

    for raw in paths:
        check()
        source = Path(os.path.abspath(raw))
        try:
            metadata = source.lstat()
        except OSError:
            # Einzeldateien behalten den bisherigen strukturierten Bild-/IO-
            # Fehler. Die Inventur selbst muss dafür keine Ausgabe erzeugen.
            add(source, None, None)
            continue
        if stat.S_ISLNK(metadata.st_mode):
            issue(source, "Symbolische Verknüpfung übersprungen.")
            continue
        if not stat.S_ISDIR(metadata.st_mode):
            if stat.S_ISREG(metadata.st_mode):
                add(source, None, metadata)
            else:
                issue(source, "Keine reguläre Bilddatei.")
            continue
        if source.suffix.lower() in PACKAGE_EXTENSIONS:
            issue(source, "App-Pakete und verwaltete Fotomediatheken werden übersprungen.")
            continue
        excluded = []
        output = destination(source / "placeholder", source)
        if output:
            excluded.append(Path(output))
        if options.output_mode == "directory" and Path(options.output_directory) != source:
            excluded.append(Path(options.output_directory))
        excluded_identities = set()
        for path in excluded:
            try:
                metadata = path.stat()
                if stat.S_ISDIR(metadata.st_mode):
                    excluded_identities.add((metadata.st_dev, metadata.st_ino))
            except OSError:
                pass
        pending = [source]
        while pending:
            check()
            directory = pending.pop()
            try:
                current = directory.lstat()
                if not stat.S_ISDIR(current.st_mode):
                    issue(directory, "Verzeichnis wurde während der Inventur ersetzt.", error=True)
                    continue
                identity = (current.st_dev, current.st_ino)
                if identity in seen_directories:
                    issue(directory, "Doppelten Ordner übersprungen.")
                    continue
                seen_directories.add(identity)
                # Nicht erst nach einem unbegrenzten list(scandir) begrenzen.
                children = []
                with os.scandir(directory) as iterator:
                    for child in iterator:
                        check()
                        children.append(child)
                children.sort(key=lambda child: child.name)
                subdirectories = []
                for child in children:
                    path = Path(child.path)
                    if child.is_symlink():
                        issue(path, "Symbolische Verknüpfung übersprungen.")
                    elif child.is_dir(follow_symlinks=False):
                        metadata = child.stat(follow_symlinks=False)
                        if path in excluded or (metadata.st_dev, metadata.st_ino) in excluded_identities:
                            issue(path, "Gewählter Ausgabebereich wird nicht erneut eingelesen.")
                        elif path.suffix.lower() in PACKAGE_EXTENSIONS:
                            issue(path, "App-Pakete und verwaltete Fotomediatheken werden übersprungen.")
                        elif options.recursive:
                            subdirectories.append(path)
                        else:
                            issue(path, "Unterordner nicht einbezogen.")
                    elif child.is_file(follow_symlinks=False) and path.suffix.lower() in IMAGE_EXTENSIONS:
                        add(path, source, child.stat(follow_symlinks=False))
                    else:
                        issue(path, "Kein unterstütztes Bildformat.")
                pending.extend(reversed(subdirectories))
            except OSError as error:
                issue(directory, f"Ordner konnte nicht vollständig eingelesen werden: {error}", error=True)
    if omitted:
        issues.append(InputIssue(str(Path(os.path.abspath(paths[0]))), f"Weitere {omitted} Einträge übersprungen."))
    return InputInventory(tuple(entries), tuple(issues))
