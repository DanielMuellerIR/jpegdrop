"""Benannte Bildoptionen und private, atomar gespeicherte Profildefinitionen."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, fields
import fcntl
import json
import os
import stat
from pathlib import Path
import tempfile
import time
import unicodedata
import uuid

import jpegdrop
from app.input_plan import InventoryOptions

MAX_PROFILES = 100
MAX_BYTES = 1024 * 1024
LOCK_TIMEOUT = 2.0
OPTION_KEYS = frozenset({"codec", "preset"} | {field.name for model in (jpegdrop.ImageOptions, InventoryOptions)
                                                for field in fields(model)})


class ProfileError(ValueError):
    pass


def normalized_name(name: str) -> str:
    if not isinstance(name, str):
        raise ProfileError("Der Profilname muss Text sein.")
    name = unicodedata.normalize("NFC", name.strip())
    if not 1 <= len(name) <= 64 or any(unicodedata.category(char) in {"Cc", "Cs"} for char in name):
        raise ProfileError("Profilnamen benötigen 1 bis 64 Zeichen ohne Steuerzeichen.")
    return name


def canonical_options(options: dict) -> tuple[tuple[str, object], ...]:
    if not isinstance(options, dict) or set(options) - OPTION_KEYS:
        raise ProfileError("Das Profil enthält unbekannte Optionen.")
    data = {key: jpegdrop.DEFAULT_SETTINGS[key] for key in OPTION_KEYS}
    data.update(options)
    codec = data["codec"]
    if not isinstance(codec, str) or codec not in jpegdrop.CODECS:
        raise ProfileError("Unbekannter Profil-Codec.")
    if "preset" not in options:
        data["preset"] = jpegdrop.CODECS[codec].default_preset
    if not isinstance(data["preset"], str) or data["preset"] not in {preset.name for preset in jpegdrop.CODECS[codec].presets}:
        raise ProfileError("Das Preset passt nicht zum Profil-Codec.")
    try:
        image = jpegdrop.ImageOptions.from_settings(data)
        InventoryOptions.from_settings(data)
        if codec == "jpeg" and image.preserve_alpha:
            raise ValueError("JPEG kann keine Transparenz erhalten.")
    except (ValueError, TypeError) as error:
        raise ProfileError(str(error)) from error
    data = jpegdrop.validated_settings(data)
    return tuple((key, data[key]) for key in sorted(OPTION_KEYS))


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    options: tuple[tuple[str, object], ...]

    def settings(self) -> dict:
        return dict(self.options)


def start_profiles() -> tuple[Profile, ...]:
    return tuple(Profile(identifier, name, canonical_options(options)) for identifier, name, options in (
        ("00000000000000000000000000000001", "Mail", {
            "codec": "jpeg", "preset": "hoch", "max_edge": 1600, "target_bytes": 500_000}),
        ("00000000000000000000000000000002", "Website-Fotos", {
            "codec": "webp", "preset": "hoch", "max_edge": 2000}),
        ("00000000000000000000000000000003", "Screenshots", {
            "codec": "png", "preset": "verlustfrei", "preserve_alpha": True}),
    ))


def active_profile(settings: dict, profiles: tuple[Profile, ...]) -> Profile | None:
    values = canonical_options({key: settings[key] for key in OPTION_KEYS})
    return next((profile for profile in profiles
                 if profile.id == settings.get("profile_id") and profile.options == values), None)


def apply_profile(settings: dict, profile: Profile) -> dict:
    return {**settings, **profile.settings(), "profile_id": profile.id}


def profile_summary(profile: Profile) -> str:
    values = profile.settings()
    codec = jpegdrop.CODECS[values["codec"]]
    preset = jpegdrop.get_preset(codec, values["preset"])
    edge = f"max. {values['max_edge']} px" if values["max_edge"] else "Originalmaße"
    limit = f" · max. {values['target_bytes']} Byte" if values["target_bytes"] else ""
    alpha = "Transparenz erhalten" if values["preserve_alpha"] else f"Hintergrund {values['background']}"
    output = {"beside": "neben der Quelle", "subfolder": f"Unterordner {values['output_subdir']}",
              "directory": f"Zielordner {values['output_directory']}"}[values["output_mode"]]
    resize = (f" · weiter verkleinern bis {values['minimum_target_edge']} px"
              if values["target_bytes"] and values["allow_target_resize"] else "")
    folders = "mit Unterordnern" if values["recursive"] else "ohne Unterordner"
    return f"{codec.label} · {preset.label} · {edge}{limit}{resize}\n{alpha} · {folders}\n{output}"


class ProfileStore:
    def __init__(self, path: Path | None = None):
        self.path = path or jpegdrop.SETTINGS_DIR / "profiles.json"

    def _read(self) -> bytes | None:
        if self.path.is_symlink():
            raise ProfileError("Eine verknüpfte Profilbibliothek wird nicht überschrieben.")
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise ProfileError("Die Profilbibliothek muss eine reguläre Datei sein.")
                raw = source.read(MAX_BYTES + 1)
        except FileNotFoundError:
            return None
        if len(raw) > MAX_BYTES:
            raise ProfileError("Die Profilbibliothek überschreitet 1 MiB.")
        return raw

    def _load(self) -> tuple[list[Profile], bytes | None]:
        raw = self._read()
        if raw is None:
            return list(start_profiles()), None
        try:
            data = json.loads(raw)
            if (not isinstance(data, dict) or set(data) != {"schema", "profiles"}
                    or type(data["schema"]) is not int or data["schema"] != 1
                    or not isinstance(data["profiles"], list) or len(data["profiles"]) > MAX_PROFILES):
                raise ProfileError("Unbekanntes oder ungültiges Profilschema; Datei bleibt erhalten.")
            profiles = []
            ids, names = set(), set()
            for item in data["profiles"]:
                if not isinstance(item, dict) or set(item) != {"id", "name", "options"}:
                    raise ProfileError("Ungültiger Profileintrag.")
                identifier = item["id"]
                if (not isinstance(identifier, str) or len(identifier) != 32
                        or any(char not in "0123456789abcdef" for char in identifier) or identifier in ids):
                    raise ProfileError("Ungültige oder doppelte Profil-ID.")
                name = normalized_name(item["name"])
                if name.casefold() in names:
                    raise ProfileError("Doppelter Profilname.")
                ids.add(identifier); names.add(name.casefold())
                profiles.append(Profile(identifier, name, canonical_options(item["options"])))
            return profiles, raw
        except ProfileError:
            raise
        except (ValueError, UnicodeError, TypeError, KeyError, RecursionError) as error:
            raise ProfileError("Profilbibliothek ist unlesbar; Datei bleibt erhalten.") from error

    def list(self) -> tuple[Profile, ...]:
        return tuple(self._load()[0])

    def by_name(self, name: str) -> Profile:
        name = normalized_name(name).casefold()
        profile = next((profile for profile in self.list() if profile.name.casefold() == name), None)
        if profile is None:
            raise ProfileError("Profil nicht gefunden.")
        return profile

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self.path.with_suffix(".json.lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "r+") as lock:
            deadline = time.monotonic() + LOCK_TIMEOUT
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ProfileError("Die Profilbibliothek wird gerade bearbeitet; bitte erneut versuchen.") from None
                    time.sleep(0.01)
            yield

    def _save(self, profiles: list[Profile], original: bytes | None) -> None:
        if len(profiles) > MAX_PROFILES:
            raise ProfileError(f"Höchstens {MAX_PROFILES} Profile sind erlaubt.")
        data = {"schema": 1, "profiles": [{"id": profile.id, "name": profile.name,
                    "options": profile.settings()} for profile in profiles]}
        raw = (json.dumps(data, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode()
        if len(raw) > MAX_BYTES:
            raise ProfileError("Die Profilbibliothek überschreitet 1 MiB.")
        descriptor, name = tempfile.mkstemp(prefix=".profiles-", dir=self.path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as target:
                target.write(raw); target.flush(); os.fsync(target.fileno())
            current = self._read()
            if current != original:
                raise ProfileError("Die Profilbibliothek wurde inzwischen geändert; bitte neu laden.")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def create(self, name: str, options: dict) -> Profile:
        name = normalized_name(name)
        values = canonical_options(options)
        with self._locked():
            profiles, original = self._load()
            if any(profile.name.casefold() == name.casefold() for profile in profiles):
                raise ProfileError("Ein Profil mit diesem Namen existiert bereits.")
            profile = Profile(uuid.uuid4().hex, name, values)
            self._save([*profiles, profile], original)
            return profile

    def update(self, identifier: str, *, name: str | None = None, options: dict | None = None,
               expected: Profile | None = None) -> Profile:
        with self._locked():
            profiles, original = self._load()
            index = next((index for index, profile in enumerate(profiles) if profile.id == identifier), None)
            if index is None:
                raise ProfileError("Profil nicht gefunden.")
            old = profiles[index]
            if expected is not None and expected != old:
                raise ProfileError("Das Profil wurde inzwischen geändert; bitte neu laden.")
            profile = Profile(old.id, normalized_name(name) if name is not None else old.name,
                              canonical_options(options) if options is not None else old.options)
            if any(other.id != identifier and other.name.casefold() == profile.name.casefold() for other in profiles):
                raise ProfileError("Ein Profil mit diesem Namen existiert bereits.")
            if profile != old:
                profiles[index] = profile
                self._save(profiles, original)
            return profile

    def delete(self, identifier: str, *, expected: Profile | None = None) -> None:
        with self._locked():
            profiles, original = self._load()
            remaining = [profile for profile in profiles if profile.id != identifier]
            if len(remaining) == len(profiles):
                raise ProfileError("Profil nicht gefunden.")
            if expected is not None and expected not in profiles:
                raise ProfileError("Das Profil wurde inzwischen geändert; bitte neu laden.")
            self._save(remaining, original)
