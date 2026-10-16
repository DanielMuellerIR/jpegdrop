"""Finder-Schnellaktion mit überprüfbarer Herkunft und sicherer Dateiübergabe."""
from __future__ import annotations

import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import tempfile

WORKFLOW_NAME = "Mit JPEGdrop konvertieren.workflow"
INSTALLED_APP = Path("/Applications/JPEGdrop.app")
BUNDLE_ID = "de.dm0.jpegdrop"
RECEIPT = "Contents/jpegdrop-receipt.json"
SCRIPT = '''set -eu
app=/Applications/JPEGdrop.app
[ "$#" -gt 0 ] || exit 0
/usr/bin/codesign --verify --deep --strict -R='anchor apple generic and identifier "de.dm0.jpegdrop"' "$app"
/usr/bin/xcrun stapler validate "$app" >/dev/null
/usr/sbin/spctl --assess --type exec "$app"
# Finder liefert absolute Pfade als Argumente, niemals als Shell-Quelltext.
/usr/bin/open -g -a "$app" -- "$@"
'''


def workflow_files() -> dict[str, bytes]:
    action = {
        "AMAccepts": {"Container": "List", "Optional": True, "Types": ["com.apple.cocoa.string"]},
        "AMProvides": {"Container": "List", "Types": ["com.apple.cocoa.string"]},
        "AMActionVersion": "2.0.3", "AMApplication": ["Automator"],
        "AMParameterProperties": {key: {} for key in ("COMMAND_STRING", "CheckedForUserDefaultShell", "inputMethod", "shell", "source")},
        "ActionBundlePath": "/System/Library/Automator/Run Shell Script.action",
        "ActionName": "Run Shell Script", "BundleIdentifier": "com.apple.RunShellScript",
        "CFBundleVersion": "2.0.3", "Class Name": "RunShellScriptAction",
        "ActionParameters": {"COMMAND_STRING": SCRIPT, "CheckedForUserDefaultShell": True,
                             "inputMethod": 1, "shell": "/bin/bash", "source": ""},
        "CanShowSelectedItemsWhenRun": False, "CanShowWhenRun": True,
        "UUID": "A9922B72-FCB4-4DC7-AC52-635B9E014B88", "isViewVisible": True,
    }
    metadata = {
        "serviceApplicationBundleID": "com.apple.finder",
        "serviceApplicationPath": "/System/Library/CoreServices/Finder.app",
        "serviceInputTypeIdentifier": "com.apple.Automator.fileSystemObject",
        "serviceOutputTypeIdentifier": "com.apple.Automator.nothing",
        "serviceProcessesInput": False, "workflowTypeIdentifier": "com.apple.Automator.servicesMenu",
        "useAutomaticInputType": False, "presentationMode": 15,
    }
    info = {"NSServices": [{"NSMenuItem": {"default": "Mit JPEGdrop konvertieren"},
        "NSMessage": "runWorkflowAsService", "NSRequiredContext": {"NSApplicationIdentifier": "com.apple.finder"},
        "NSSendFileTypes": ["public.item"]}]}
    files = {
        "Contents/Info.plist": plistlib.dumps(info),
        "Contents/document.wflow": plistlib.dumps({"AMDocumentVersion": "2", "AMApplicationBuild": "523",
            "AMApplicationVersion": "2.10", "actions": [{"action": action}], "connectors": {}, "workflowMetaData": metadata}),
    }
    files[RECEIPT] = (json.dumps({"owner": BUNDLE_ID, "schema": 1,
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}, sort_keys=True) + "\n").encode()
    return files


def verify_installed_app() -> None:
    commands = [
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", "-R=anchor apple generic and identifier \"de.dm0.jpegdrop\"", str(INSTALLED_APP)],
        ["/usr/bin/xcrun", "stapler", "validate", str(INSTALLED_APP)],
        ["/usr/sbin/spctl", "--assess", "--type", "exec", str(INSTALLED_APP)],
    ]
    for command in commands:
        try:
            result = subprocess.run(command, capture_output=True, timeout=30)
        except subprocess.TimeoutExpired as error:
            raise ValueError("Die Prüfung der installierten JPEGdrop-App hat zu lange gedauert.") from error
        if result.returncode:
            raise ValueError("Bitte zuerst eine gültig signierte und notarisierte JPEGdrop-App nach /Applications installieren.")


def rename_exclusive(source: Path, destination: Path) -> None:
    # rename(2) dürfte ein fremdes leeres Verzeichnis ersetzen. RENAME_EXCL
    # schützt auch dann, wenn zwischen Prüfung und Veröffentlichung jemand anlegt.
    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    rename = libc.renamex_np
    rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(os.fsencode(source), os.fsencode(destination), 0x00000004):
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number), str(destination))


class FinderAction:
    def __init__(self, services: Path | None = None):
        self.services = services if services is not None else Path.home() / "Library/Services"
        self.path = self.services / WORKFLOW_NAME

    def _owned_files(self, root: Path) -> dict[str, bytes]:
        try:
            if root.is_symlink() or not root.is_dir():
                raise ValueError()
            files = {}
            for entry in root.rglob("*"):
                mode = entry.lstat().st_mode
                if entry.is_symlink() or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                    raise ValueError()
                name = str(entry.relative_to(root))
                if stat.S_ISDIR(mode):
                    if name != "Contents":
                        raise ValueError()
                else:
                    if entry.stat().st_size > 1024 * 1024:
                        raise ValueError()
                    files[name] = entry.read_bytes()
            receipt = json.loads(files[RECEIPT])
            if (set(receipt) != {"schema", "owner", "files"} or type(receipt["schema"]) is not int
                    or receipt["schema"] != 1 or receipt["owner"] != BUNDLE_ID
                    or set(receipt["files"]) != {"Contents/Info.plist", "Contents/document.wflow"}
                    or set(files) != {*receipt["files"], RECEIPT}
                    or any(hashlib.sha256(files[name]).hexdigest() != digest for name, digest in receipt["files"].items())):
                raise ValueError()
            return files
        except (ValueError, KeyError, TypeError, UnicodeError, OSError) as error:
            raise ValueError("Die vorhandene Schnellaktion ist fremd oder wurde geändert; sie bleibt erhalten.") from error

    def _lock(self):
        self.services.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self.services / ".jpegdrop-finder.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        lock = os.fdopen(descriptor, "r+")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise ValueError("Die Finder-Schnellaktion wird gerade bearbeitet; bitte erneut versuchen.") from None
        return lock

    def install(self) -> str:
        verify_installed_app()
        with self._lock():
            if self.path.exists() or self.path.is_symlink():
                self._owned_files(self.path)
                return "Die JPEGdrop-Schnellaktion ist bereits eingerichtet."
            temporary = Path(tempfile.mkdtemp(prefix=".jpegdrop-finder-", dir=self.services))
            try:
                (temporary / "Contents").mkdir(mode=0o700)
                for name, data in workflow_files().items():
                    target = temporary / name
                    with target.open("xb") as output:
                        os.chmod(target, 0o600)
                        output.write(data)
                rename_exclusive(temporary, self.path)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        return "Finder-Schnellaktion eingerichtet. Falls nötig unter Systemeinstellungen → Allgemein → Anmeldeobjekte & Erweiterungen → Finder aktivieren."

    def remove(self) -> str:
        with self._lock():
            if not self.path.exists() and not self.path.is_symlink():
                return "Die JPEGdrop-Schnellaktion ist nicht eingerichtet."
            original = self._owned_files(self.path)
            temporary = Path(tempfile.mkdtemp(prefix=".jpegdrop-finder-", dir=self.services))
            moved = temporary / WORKFLOW_NAME
            try:
                rename_exclusive(self.path, moved)
                try:
                    if self._owned_files(moved) != original:
                        raise ValueError("Die Schnellaktion wurde inzwischen geändert; sie bleibt erhalten.")
                except (ValueError, OSError):
                    rename_exclusive(moved, self.path)
                    raise
                for name in original:
                    (moved / name).unlink()
                (moved / "Contents").rmdir()
                moved.rmdir()
            finally:
                if not moved.exists():
                    temporary.rmdir()
        return "JPEGdrop-Schnellaktion entfernt."
