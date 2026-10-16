#!/usr/bin/env python3
"""jpegdrop — adaptives, metadatenfreies Bild-Komprimieren.

Gedacht als Drop-Ziel fürs macOS-Dock (siehe build.sh / Droplet), funktioniert
aber genauso headless auf der Kommandozeile und ist damit von AI-Agenten
steuerbar (Flags, Exit-Codes, --json für maschinenlesbaren Output).

Was es macht — pro Bild:
  - Qualität nach Preset (Default "hoch", q85). Preset "auto" = adaptive Kurve:
    kleine Bilder hohe Qualität, große (>1200 px) niedrigere — spart viel,
    sieht trotzdem gut aus
  - HEIC/HEIF (z.B. iPhone), PNG, TIFF, WEBP, AVIF ... -> wird in den gewählten
    Ziel-Codec (JPEG / PNG / AVIF / WebP) transcodiert
  - Eingebettete Metadaten weg: EXIF, GPS, Maker-Notes, Zeitstempel, ICC-Profil
  - EXIF-Orientierung wird VORHER ins Pixelbild eingerechnet
    (sonst stünde ein iPhone-Foto nach dem Strippen schief)
  - Nicht-sRGB-Farbprofile (z.B. iPhone Display-P3) werden nach sRGB umgerechnet,
    bevor das Profil entfernt wird -> Farben bleiben korrekt, ganz ohne Metadaten
  - Transparenz wird derzeit bei allen Ziel-Codecs auf Weiß flachgelegt;
    tatsächlicher Transparenzverlust wird als Warnung gemeldet
  - Mehrbildquellen und nicht anwendbare Farbprofile werden abgelehnt

Zieldatei liegt NEBEN der Quelle. Name:
  - Codec entspricht der Quell-Endung NICHT -> gleicher Name, neue Codec-Endung
  - Codec entspricht der Quell-Endung           -> Suffix "-klein" anhängen
    (z.B. PNG -> PNG, JPEG -> JPEG, AVIF -> AVIF), sonst Kollision
  - existiert der Zielname schon                -> Zähler dran (-2, -3, ...),
                                                  nie überschreiben

Einstellungen (Codec + Preset) werden in der GUI über einen ⌘,-Dialog gesetzt
und liegen in ~/Library/Application Support/JPEGdrop/settings.json. Auf der
CLI sind sie Default, falls nicht überschrieben.
"""

from __future__ import annotations

import argparse
import errno
import io
import json
import os
import signal
import shutil
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageOps, ImageCms

__version__ = "2.6.1"

# HEIC/HEIF-Decoder bei Pillow registrieren (für iPhone-Aufnahmen).
# Optional gehalten: fehlt das Paket, läuft alles außer HEIC weiter.
try:
    import pi_heif

    pi_heif.register_heif_opener()
except Exception:  # pragma: no cover - nur falls Abhängigkeit fehl
    pass


# ===========================================================================
# Codecs & Presets
# ===========================================================================
#
# Codec-Tabelle: jeder Codec kennt
#   - extension : Datei-Endung mit Punkt, wird fürs Ziel genutzt
#   - save_kwargs(img, preset) -> dict mit Pillow-Save-Optionen
#   - description : kurze, ehrliche Erklärung was der Codec macht
#
# Presets sind pro Codec eine Tuple von Preset-Dataclass-Instanzen (name, label,
# description, extra). "Auto" ist ein besonderes Preset (siehe unten) und gilt für JPEG/AVIF/WebP.
#
# HEIC ist ein Eingabeformat. Der reine HEIF-Decoder vermeidet ungenutzte
# Encoder im Bundle; die Ausgabe-Codecs werden von Pillow bereitgestellt.
# ===========================================================================


@dataclass(frozen=True)
class Preset:
    """Ein einzelnes Preset innerhalb eines Codecs."""

    name: str          # interner Schlüssel, z.B. "maximal"
    label: str         # UI-Text, z.B. "Maximal (q95)"
    description: str   # kurze Erklärung (für UI-Tooltip / README)
    # 'extra' steuert optionale Vorverarbeitung, derzeit die Farbzahl des
    # Paletten-PNG-Presets.
    extra: dict[str, Any] | None = None


@dataclass(frozen=True)
class Codec:
    name: str          # "jpeg", "png", "avif", "webp"
    label: str         # "JPEG", "PNG", "AVIF", "WebP"
    extension: str     # ".jpg", ".png", ".avif", ".webp"
    description: str   # ein Satz für UI / README
    presets: tuple[Preset, ...]
    default_preset: str  # sicherer Default bei Codec-Wechseln
    # Bevorzugte Endungen, die als "schon in diesem Codec" gelten
    # (für die Suffix-Logik jpg->jpg).
    native_extensions: tuple[str, ...] = ()


# -- JPEG --------------------------------------------------------------------
# Presets nach User-Wunsch: 95/85/70/55/30/15.
# Erklärung "Auto": die klassische adaptive Kurve aus v1.0
# (große Bilder aggressiver komprimieren).
JPEG_PRESETS = (
    Preset("maximal",    "Maximal (q95)",     "Höchste JPEG-Qualität; weiterhin verlustbehaftet, kein Archivoriginal.", None),
    Preset("hoch",       "Hoch (q85)",        "Sehr gute Qualität, normale Dateigröße.",         None),
    Preset("mittel",     "Mittel (q70)",      "Guter Allround-Sweet-Spot.",                     None),
    Preset("klein",      "Klein (q55)",       "Spürbar kleiner, fürs Web / Messenger.",         None),
    Preset("sehr_klein", "Sehr klein (q30)",  "Aggressiv komprimiert, sichtbarer Verlust.",     None),
    Preset("superklein", "Superklein (q15)",  "Minimalkonvert — fast nur noch Farbflächen.",    None),
)

# "Auto" als expliziter Eintrag in der Preset-Liste mit Namen "auto",
# damit UI / CLI einheitlich damit umgehen können.
JPEG_PRESETS_WITH_AUTO = (
    Preset(
        "auto", "Auto (adaptive Kurve)",
        "Klassisch: kleine Bilder hochwertig, große Bilder aggressiver komprimiert (v1.0-Verhalten).",
        None,
    ),
) + JPEG_PRESETS


def _jpeg_save_kwargs(preset_name: str, auto_quality: int) -> dict[str, Any]:
    """Mappt Preset -> Pillow-Save-Kwargs für JPEG."""
    table = {
        "maximal":    {"quality": 95, "optimize": True, "progressive": True},
        "hoch":       {"quality": 85, "optimize": True, "progressive": True},
        "mittel":     {"quality": 70, "optimize": True, "progressive": True},
        "klein":      {"quality": 55, "optimize": True, "progressive": True},
        "sehr_klein": {"quality": 30, "optimize": True, "progressive": True},
        "superklein": {"quality": 15, "optimize": True, "progressive": True},
        "auto":       {"quality": auto_quality, "optimize": True, "progressive": True},
    }
    return table[preset_name]


# -- PNG ---------------------------------------------------------------------
# 2 Stufen: verlustfrei (Standard, full optimize) und 256-Farben-Palette
# (deutlich kleiner, sichtbarer Verlust bei Fotos, ok fürs Web/UI).
PNG_PRESETS = (
    Preset(
        "verlustfrei", "Verlustfreie PNG-Kodierung",
        "Keine zusätzlichen Kodierverluste. Vorher werden Farben nach RGB/sRGB "
        "umgerechnet und Transparenz auf Weiß gelegt.",
        None,
    ),
    Preset(
        "klein_256", "Klein (256 Farben)",
        "Paletten-PNG mit 256 Farben. Für Fotos sichtbarer Verlust, "
        "für UI-Grafiken/Illustrationen oft kaum sichtbar — und deutlich kleiner.",
        {"palette_colors": 256},
    ),
)

# PNG hat kein "Auto" (verlustfrei ist immer gleich gut), aber wir lassen
# den Namen aus Konsistenzgründen als No-op-Preset zu, das aufs verlustfreie
# Preset mapped.
PNG_PRESETS_WITH_AUTO = (
    Preset("auto", "Auto (= PNG verlustfrei kodiert)", "Identisch mit verlustfreier PNG-Kodierung nach der Bildvorbereitung.", None),
) + PNG_PRESETS


def _png_save_kwargs(preset_name: str) -> dict[str, Any]:
    """Mappt Preset -> Pillow-Save-Kwargs für PNG."""
    if preset_name in ("auto", "verlustfrei"):
        return {"format": "PNG", "optimize": True}
    if preset_name == "klein_256":
        return {"format": "PNG", "optimize": True}
    raise ValueError(f"Unbekanntes PNG-Preset: {preset_name}")


# -- AVIF --------------------------------------------------------------------
# Sehr ähnlich zu JPEG in der Preset-Aufteilung, aber AVIF komprimiert
# bei gleichem q-Wert deutlich besser. HEIC-Ersatz.
AVIF_PRESETS = (
    Preset("maximal",    "Maximal (q90)",     "Höchste Qualität.",                            None),
    Preset("hoch",       "Hoch (q70)",        "Sehr gute Qualität, kleiner als JPEG.",        None),
    Preset("mittel",     "Mittel (q55)",      "Guter Allround-Sweet-Spot.",                   None),
    Preset("klein",      "Klein (q40)",       "Web-/Messenger-tauglich.",                     None),
    Preset("sehr_klein", "Sehr klein (q25)",  "Aggressiv klein.",                             None),
    Preset("superklein", "Superklein (q15)",  "Minimalkonvert.",                              None),
)
AVIF_PRESETS_WITH_AUTO = (
    Preset("auto", "Auto (adaptive Kurve)", "Wie bei JPEG: große Bilder aggressiver komprimieren.", None),
) + AVIF_PRESETS


def _avif_save_kwargs(preset_name: str, auto_quality: int) -> dict[str, Any]:
    """Mappt Preset -> Pillow-Save-Kwargs für AVIF. speed=6 ist Default der libaom."""
    table = {
        "maximal":    {"quality": 90, "speed": 6},
        "hoch":       {"quality": 70, "speed": 6},
        "mittel":     {"quality": 55, "speed": 6},
        "klein":      {"quality": 40, "speed": 6},
        "sehr_klein": {"quality": 25, "speed": 6},
        "superklein": {"quality": 15, "speed": 6},
        "auto":       {"quality": auto_quality, "speed": 6},
    }
    return table[preset_name]


# -- WebP --------------------------------------------------------------------
# Optionaler Codec, in Pillow direkt dabei. Hat verlustfrei + verlustbehaftet.
WEBP_PRESETS = (
    Preset("maximal",    "Maximal (q90)",     "Höchste Qualität.",         None),
    Preset("hoch",       "Hoch (q75)",        "Sehr gute Qualität.",       None),
    Preset("mittel",     "Mittel (q60)",      "Guter Sweet-Spot.",         None),
    Preset("klein",      "Klein (q45)",       "Web/Messenger.",            None),
    Preset("sehr_klein", "Sehr klein (q30)",  "Aggressiv.",                None),
    Preset("superklein", "Superklein (q15)",  "Minimalkonvert.",           None),
)
WEBP_PRESETS_WITH_AUTO = (
    Preset("auto", "Auto (adaptive Kurve)", "Adaptive Qualitätskurve wie bei JPEG.", None),
) + WEBP_PRESETS


def _webp_save_kwargs(preset_name: str, auto_quality: int) -> dict[str, Any]:
    """Mappt Preset -> Pillow-Save-Kwargs für WebP (verlustbehaftet, method=6 = beste Kompression)."""
    table = {
        "maximal":    {"quality": 90, "method": 6},
        "hoch":       {"quality": 75, "method": 6},
        "mittel":     {"quality": 60, "method": 6},
        "klein":      {"quality": 45, "method": 6},
        "sehr_klein": {"quality": 30, "method": 6},
        "superklein": {"quality": 15, "method": 6},
        "auto":       {"quality": auto_quality, "method": 6},
    }
    return table[preset_name]


# -- Codec-Registry ----------------------------------------------------------
CODECS: dict[str, Codec] = {
    "jpeg": Codec(
        name="jpeg",
        label="JPEG",
        extension=".jpg",
        description="Universell kompatibel. Verlustbehaftet. Kein Alpha.",
        presets=JPEG_PRESETS_WITH_AUTO,
        default_preset="hoch",
        native_extensions=(".jpg", ".jpeg"),
    ),
    "png": Codec(
        name="png",
        label="PNG",
        extension=".png",
        description="Verlustfreie Kodierung nach RGB/sRGB-Umrechnung. Transparenz wird auf Weiß gelegt.",
        presets=PNG_PRESETS_WITH_AUTO,
        default_preset="verlustfrei",
        native_extensions=(".png",),
    ),
    "avif": Codec(
        name="avif",
        label="AVIF",
        extension=".avif",
        description="Modern, sehr effizient. HEIC-Ersatz. macOS Sonoma+ nativ lesbar.",
        presets=AVIF_PRESETS_WITH_AUTO,
        default_preset="hoch",
        native_extensions=(".avif",),
    ),
    "webp": Codec(
        name="webp",
        label="WebP",
        extension=".webp",
        description="Effizient, breit unterstützt. Verlustbehaftet hier (kein Alpha-Output).",
        presets=WEBP_PRESETS_WITH_AUTO,
        default_preset="hoch",
        native_extensions=(".webp",),
    ),
}


def get_codec(name: str) -> Codec:
    """Codec per Name (CLI-arg). Wirft KeyError mit klarer Meldung."""
    try:
        return CODECS[name.lower()]
    except KeyError:
        valid = ", ".join(sorted(CODECS))
        raise SystemExit(f"Unbekannter Codec '{name}'. Verfügbar: {valid}") from None


def get_preset(codec: Codec, name: str) -> Preset:
    """Preset per Name innerhalb eines Codecs."""
    for p in codec.presets:
        if p.name == name:
            return p
    valid = ", ".join(p.name for p in codec.presets)
    raise SystemExit(
        f"Unbekanntes Preset '{name}' für Codec '{codec.name}'. Verfügbar: {valid}"
    )


def list_presets_text() -> str:
    """Schöner Text für `jpegdrop.py --list-presets` (auch für die UI)."""
    lines = ["Verfügbare Codecs und Presets:", ""]
    for codec in CODECS.values():
        lines.append(f"  {codec.label}  (--codec {codec.name})")
        lines.append(f"    {codec.description}")
        for p in codec.presets:
            lines.append(f"    - {p.name:12s}  {p.label}")
            if p.description:
                lines.append(f"                  {p.description}")
        lines.append("")
    return "\n".join(lines)


# ===========================================================================
# Adaptive Auto-Kurve (aus v1.0 unverändert übernommen).
# Längere Bildkante -> niedrigere Qualität.
# ===========================================================================
QUALITY_STEPS = [
    # (max_kante_bis_einschließlich, qualitaet)
    (1200, 90),   # klein  -> nahezu verlustfrei wahrgenommen
    (2000, 82),
    (3000, 76),
    (4000, 71),
]
QUALITY_FLOOR = 66  # alles größer als die letzte Schwelle (z.B. 4032-px-iPhone-Foto)


def pick_quality(max_edge: int) -> int:
    """Adaptive Auto-Qualität (nur für JPEG/AVIF/WebP benutzt)."""
    for limit, q in QUALITY_STEPS:
        if max_edge <= limit:
            return q
    return QUALITY_FLOOR


# ===========================================================================
# Zielpfad + Suffix-Logik
# ===========================================================================
DEFAULT_SUFFIX = "-klein"


def human_size(num_bytes: int) -> str:
    """Bytes -> kurzer lesbarer String (KB/MB)."""
    if num_bytes >= 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.1f} MB"
    return f"{num_bytes / 1024:.0f} KB"


# Praefix der Zwischendatei, die neben dem Ziel entsteht. Bewusst kurz und
# unabhängig vom Quellnamen: frueher stand hier der volle Ziel-Stem, wodurch mkstemps acht
# Zufallszeichen den Namen ueber NAME_MAX (255 Bytes) hinaus wachsen liessen —
# lange, an sich gueltige Dateinamen scheiterten dann mit ENAMETOOLONG.
TEMP_PREFIX = ".jpegdrop-"


def temporary_prefix() -> str:
    """Ein GUI-Auftrag darf nach einem harten Abbruch nur eigene Reste entfernen."""
    job_id = os.environ.get("JPEGDROP_JOB_ID", "")
    if len(job_id) == 32 and all(c in "0123456789abcdef" for c in job_id):
        return f"{TEMP_PREFIX}{job_id}-"
    return TEMP_PREFIX

# Groesste erlaubte Laenge EINES Dateinamens in Bytes (NAME_MAX). APFS, HFS+ und
# die ueblichen externen Dateisysteme liegen bei 255; laengere Namen lehnt schon
# das Dateisystem mit ENAMETOOLONG ab.
NAME_MAX = 255

# Einmal ermittelter Dateimodus fuer die Bildausgabe (None = noch nicht gelesen).
_PUBLISH_MODE: int | None = None
# Die Ermittlung verstellt kurz die prozessweite umask. Zwei Threads duerfen
# sich dabei nicht ueberlappen: Sonst liest der zweite den schon verstellten
# Wert als "Original" und stellt am Ende genau den wieder her — der Prozess
# behielte eine falsche umask, und spaeter angelegte Dateien bekaemen zu enge
# Rechte. Die Sperre schuetzt eine moegliche mehrfaedige Einbettung; der
# Regressionstest ruft publish_mode() deshalb gezielt aus zwei Threads auf.
_PUBLISH_MODE_LOCK = threading.Lock()


def fit_name(stem: str, extra: str, suffix: str) -> str:
    """Kuerzt `stem`, bis `stem + extra + suffix` in NAME_MAX passt.

    Gemessen wird in Bytes, denn NAME_MAX zaehlt Bytes und nicht Zeichen — ein
    Umlaut braucht in UTF-8 zwei. Die Schleife nimmt nur vollstaendige Zeichen
    mit; surrogateescape-Zeichen fuer rohe Dateinamensbytes bleiben dabei erhalten.

    Ohne diese Kuerzung scheitern gueltige Quellnamen: Ein Stem mit 251 Bytes
    und ``.png`` ergibt erlaubte 255 Bytes, waechst mit Namens-Suffix und neuer
    Endung aber darueber hinaus — und der Kollisionszaehler von
    ``unique_target`` legt noch einmal zu (ENAMETOOLONG).
    """
    # Python bildet nicht-UTF-8-faehige Dateinamen von Fremddateisystemen mit
    # surrogateescape ab. Derselbe Fehlerkanal muss beim Kuerzen erhalten
    # bleiben, damit die spaeteren os-Aufrufe die Originalbytes zurueckbekommen.
    reserviert = len(extra.encode("utf-8", "surrogateescape")) + len(
        suffix.encode("utf-8", "surrogateescape")
    )
    roh = stem.encode("utf-8", "surrogateescape")
    if len(roh) + reserviert <= NAME_MAX:
        return stem
    budget = max(0, NAME_MAX - reserviert)
    teile: list[str] = []
    verwendet = 0
    for zeichen in stem:
        laenge = len(zeichen.encode("utf-8", "surrogateescape"))
        if verwendet + laenge > budget:
            break
        teile.append(zeichen)
        verwendet += laenge
    return "".join(teile)


def publish_mode() -> int:
    """Dateimodus der fertigen Bildausgabe: was eine normal angelegte Datei bekaeme.

    ``mkstemp`` legt die Zwischendatei absichtlich als 0600 an. Die fertige
    Ausgabe soll aber so aussehen wie damals, als Pillow direkt ins Ziel
    geschrieben hat — also ``0666`` abzueglich der umask des Benutzers. Ein
    festes 0644 waere zwar bei der ueblichen umask 022 dasselbe, wuerde aber
    eine restriktive umask (etwa 077, "nur ich darf lesen") aushebeln.

    Die umask laesst sich nur durch Setzen lesen; deshalb wird sie kurz auf
    einen restriktiveren Wert gesetzt und sofort zurueckgestellt. Das Ergebnis
    wird prozessweit gemerkt, damit dieses Fenster genau einmal auftritt — und
    die Sperre sorgt dafuer, dass sich zwei Threads dieses eine Fenster nicht
    teilen (siehe _PUBLISH_MODE_LOCK).
    """
    global _PUBLISH_MODE
    if _PUBLISH_MODE is None:
        with _PUBLISH_MODE_LOCK:
            # Innerhalb der Sperre noch einmal nachsehen: Wer hier gewartet hat,
            # findet den Wert bereits fertig vor und darf die umask nicht ein
            # zweites Mal anfassen.
            if _PUBLISH_MODE is None:
                previous = os.umask(0o077)
                os.umask(previous)
                _PUBLISH_MODE = 0o666 & ~previous
    return _PUBLISH_MODE


def unique_target(base: Path, name_suffix: str = "") -> Path:
    """Existiert `base` schon, wird -2, -3 ... vor die Endung gehängt.

    Bei nativen Quellen bleibt ``name_suffix`` vollstaendig erhalten. Gekuerzt
    wird nur der eigentliche Quell-Stem, damit ein langes ``-klein``-Ziel auch
    nach einer Kollision als JPEGdrop-Ausgabe erkennbar bleibt.
    """
    if not os.path.lexists(base):
        return base
    stem, suffix, parent = base.stem, base.suffix, base.parent
    if name_suffix and stem.endswith(name_suffix):
        source_stem = stem[: -len(name_suffix)]
        protected_suffix = name_suffix
    else:
        source_stem = stem
        protected_suffix = ""
    n = 2
    while True:
        zaehler = f"-{n}"
        # Der Zaehler darf den Namen nicht ueber NAME_MAX schieben: Ein bereits
        # maximal langer Zielname waere sonst genau im Kollisionsfall unbrauchbar.
        extra = f"{protected_suffix}{zaehler}"
        extra_bytes = len(extra.encode("utf-8", "surrogateescape"))
        suffix_bytes = len(suffix.encode("utf-8", "surrogateescape"))
        if extra_bytes + suffix_bytes > NAME_MAX:
            fitted_suffix = fit_name(protected_suffix, zaehler, suffix)
            cand_extra = f"{fitted_suffix}{zaehler}"
            fitted_stem = ""
        else:
            cand_extra = extra
            fitted_stem = fit_name(source_stem, cand_extra, suffix)

        cand = parent / f"{fitted_stem}{cand_extra}{suffix}"
        if not os.path.lexists(cand):
            return cand
        n += 1


def target_base_path(src: Path, codec: Codec, name_suffix: str) -> Path:
    """Bestimmt den noch nicht hochgezählten Zielpfad neben der Quelle.

    Logik:
      - Endung der Quelle ist native für diesen Codec -> Suffix anhängen
        (sonst überschreiben wir die Quelle).
      - sonst -> gleicher Name, neue Codec-Endung.
    """
    src_ext = src.suffix.lower()
    # Stem notfalls kuerzen: Suffix und neue Endung koennen einen gerade noch
    # erlaubten Quellnamen ueber NAME_MAX hinaus wachsen lassen (siehe fit_name).
    if src_ext in codec.native_extensions:
        stem = fit_name(src.stem, name_suffix, codec.extension)
        return src.with_name(f"{stem}{name_suffix}{codec.extension}")
    stem = fit_name(src.stem, "", codec.extension)
    return src.with_name(f"{stem}{codec.extension}")


def publish_unique(
    temp_path: Path,
    base: Path,
    *,
    source_fd: int | None = None,
    name_suffix: str = "",
) -> Path:
    """Veröffentlicht eine fertige Temp-Datei atomar unter einem freien Namen.

    ``exists()`` plus anschließendes Schreiben hat ein TOCTOU-Fenster: zwei
    App-Worker konnten denselben Namen wählen und sich überschreiben. Der
    Hardlink entsteht dagegen atomar und schlägt bei einem bereits belegten
    Ziel fehl. Dann wird einfach der nächste Zählername probiert. Wo ein
    Dateisystem keine Hardlinks erlaubt, übernimmt O_EXCL dieselbe Reservierung.
    Temp-Datei und Ziel liegen bewusst im selben Verzeichnis.
    """
    hardlink_supported = True
    unsupported_link_errors = {
        errno.EXDEV,
        errno.EPERM,
        errno.EMLINK,
        getattr(errno, "ENOTSUP", errno.EOPNOTSUPP),
        errno.EOPNOTSUPP,
    }
    while True:
        candidate = unique_target(base, name_suffix)
        if hardlink_supported:
            try:
                os.link(temp_path, candidate)
                return candidate
            except FileExistsError:
                # Ein anderer Prozess war zwischen unique_target() und os.link()
                # schneller. Neu zählen, ohne dessen Ausgabe anzufassen.
                continue
            except OSError as exc:
                if exc.errno not in unsupported_link_errors:
                    raise
                hardlink_supported = False

        # Manche externen bzw. Netz-Dateisysteme erlauben keine Hardlinks.
        # O_EXCL reserviert auch dort atomar; in diese eigene Reservation wird
        # die bereits vollständig codierte Datei kopiert.
        try:
            target_fd = os.open(
                candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, publish_mode()
            )
        except FileExistsError:
            continue
        try:
            # _process_one oeffnet die Temp-Datei, bevor sie ihren endgueltigen
            # (moeglicherweise schreibgeschuetzten) Modus bekommt. Der Fallback
            # liest ueber diesen Deskriptor statt den Pfad erneut zu oeffnen.
            with os.fdopen(target_fd, "wb") as target:
                source_handle = (
                    os.fdopen(os.dup(source_fd), "rb")
                    if source_fd is not None
                    else temp_path.open("rb")
                )
                source_handle.seek(0)
                with source_handle as source:
                    shutil.copyfileobj(source, target)
                    target.flush()
                    os.fsync(target.fileno())
        except BaseException:
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return candidate


# ===========================================================================
# Bildverarbeitung
# ===========================================================================
class ImageInputError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def normalize_image(img: Image.Image, *, notices: list[dict] | None = None) -> Image.Image:
    """Bringt das Bild in einen sauberen, Codec-tauglichen sRGB-RGB-Zustand:
    Orientierung einrechnen, Farbprofil nach sRGB backen, Transparenz flachlegen.
    Gibt ein RGB-Bild ohne nutzbare Metadaten im .info zurück."""

    # 1) EXIF-Orientierung ins Pixelbild einrechnen, solange wir die EXIF noch
    #    haben. Danach darf die Orientierung gefahrlos verworfen werden.
    img = ImageOps.exif_transpose(img)

    # Farbprofil merken, BEVOR wir den Modus ändern (Konvertierungen können
    # das ICC-Profil aus .info verlieren).
    icc = img.info.get("icc_profile")

    # Transparenz-Schlüsselwerte (z. B. PNG tRNS in RGB-, L- oder P-Modus)
    # vor der Profilumrechnung und dem Entfernen der Metadaten in einen
    # expliziten Alphakanal überführen.
    if "transparency" in img.info and img.mode not in ("RGBA", "LA"):
        img = img.convert("LA" if img.mode == "L" else "RGBA")

    # 2) Das Profil im ursprünglichen Farbmodus anwenden. CMYK zuerst nach
    #    RGB zu konvertieren verwirft die Farbinformationen für das CMYK-ICC.
    if icc is not None:
        try:
            if not isinstance(icc, (bytes, bytearray, memoryview)):
                raise ValueError("Ungültiger ICC-Datentyp")
            src_profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            srgb_profile = ImageCms.createProfile("sRGB")
            # Paletten sind eine Kodierung, kein ICC-Eingabefarbraum. Alpha
            # bleibt bis zum anschließenden Zusammensetzen mit Weiß erhalten.
            profile_image = img
            if img.mode == "P":
                profile_image = img.convert("RGBA" if "transparency" in img.info else "RGB")
            output_mode = "RGBA" if "A" in profile_image.getbands() else "RGB"
            img = ImageCms.profileToProfile(
                profile_image, src_profile, srgb_profile, outputMode=output_mode
            )
        except (ImageCms.PyCMSError, OSError, ValueError) as exc:
            raise ImageInputError(
                "invalid_color_profile",
                "Das eingebettete Farbprofil ist unlesbar oder passt nicht zum Bild. "
                "Eine verlässliche Umrechnung nach sRGB ist nicht möglich.",
            ) from exc

    # 3) Transparenz auf weißem Grund flachlegen (JPEG kennt kein Alpha).
    if "A" in img.getbands() or img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        if notices is not None and img.getchannel("A").getextrema()[0] < 255:
            notices.append({
                "code": "transparency_flattened",
                "message": "Transparenz wurde auf weißem Hintergrund entfernt.",
            })
        background = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(background, img).convert("RGB")
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # 4) Sicherstellen, dass keine Metadaten am Bild hängen bleiben.
    img.info = {}
    return img


def _save_kwargs(
    codec: Codec,
    preset_name: str,
    max_edge: int,
    fixed_quality: int | None,
) -> dict[str, Any]:
    """Erzeugt die tatsächlich verwendeten Pillow-Speicheroptionen."""
    if fixed_quality is not None:
        return {
            "format": "JPEG",
            "quality": fixed_quality,
            "optimize": True,
            "progressive": True,
        }

    auto_q = pick_quality(max_edge)
    if codec.name == "jpeg":
        return {"format": "JPEG", **_jpeg_save_kwargs(preset_name, auto_q)}
    if codec.name == "png":
        return _png_save_kwargs(preset_name)
    if codec.name == "avif":
        return {"format": "AVIF", **_avif_save_kwargs(preset_name, auto_q)}
    if codec.name == "webp":
        return {"format": "WEBP", **_webp_save_kwargs(preset_name, auto_q)}
    raise SystemExit(f"Interner Fehler: Codec '{codec.name}' hat keinen Save-Builder.")


def _prepare_for_preset(
    img: Image.Image,
    codec: Codec,
    preset_name: str,
) -> Image.Image:
    """Wendet die zum Preset gehörige Pixel-Vorverarbeitung an."""
    if codec.name != "png":
        return img

    preset = get_preset(codec, preset_name)
    palette_colors = (preset.extra or {}).get("palette_colors")
    if palette_colors is None:
        return img

    # Pillow-Save-Optionen quantisieren ein RGB-Bild nicht. Die explizite
    # Umwandlung erzeugt wirklich ein Paletten-PNG mit höchstens 256 Farben.
    quantized = img.quantize(
        colors=palette_colors,
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.FLOYDSTEINBERG,
    )
    quantized.info = {}
    return quantized


def empty_result(src: Path, codec_name: str, preset_name: str) -> dict:
    """Gemeinsamer Ergebnisvertrag, auch für noch nicht verarbeitete Auftragsbilder."""
    return {
        "source": str(src), "target": None, "src_bytes": None, "out_bytes": None,
        "codec": codec_name, "preset": preset_name, "quality": None,
        "max_edge": None, "error": None, "status": "error",
        "error_code": None, "warnings": [],
    }


def _process_one(
    src: Path,
    codec: Codec,
    preset_name: str,
    name_suffix: str,
    *,
    fixed_quality: int | None = None,
    on_event: Callable[[str, dict | None], None] | None = None,
) -> dict:
    """Gemeinsame Lade-, Normalisierungs-, Speicher- und Fehlerpipeline."""
    result = empty_result(src, codec.name, preset_name)
    try:
        if not src.is_file():
            raise ImageInputError("source_not_found", "Datei nicht gefunden")

        src_bytes = src.stat().st_size
        result["src_bytes"] = src_bytes

        with Image.open(src) as opened:
            frame_count = getattr(opened, "n_frames", 1)
            if frame_count > 1:
                raise ImageInputError(
                    "multiple_images",
                    f"{opened.format or 'Bilddatei'} enthält {frame_count} Bilder/Seiten. "
                    "Animationen und Mehrbilddateien werden nicht in ein Einzelbild umgewandelt.",
                )
            opened.load()
            img = normalize_image(opened, notices=result["warnings"])

        max_edge = max(img.size)
        result["max_edge"] = max_edge

        save_kwargs = _save_kwargs(codec, preset_name, max_edge, fixed_quality)
        result["quality"] = save_kwargs.get("quality")
        img = _prepare_for_preset(img, codec, preset_name)

        base_path = target_base_path(src, codec, name_suffix)
        fd, temp_name = tempfile.mkstemp(
            prefix=temporary_prefix(),
            suffix=codec.extension,
            dir=base_path.parent,
        )
        os.close(fd)
        temp_path = Path(temp_name)
        # mkstemp will 0600, aber die umask des Benutzers greift auch hier: Bei
        # einer sehr restriktiven umask (etwa 0477) entstuende 0200 — die Datei
        # waere fuer uns selbst nicht mehr lesbar, und schon Pillow oeffnet sie
        # zum Schreiben mit Lesezugriff. Deshalb den gedachten Wert erzwingen;
        # den endgueltigen Modus bekommt erst die veroeffentlichte Datei.
        source_fd: int | None = None
        try:
            temp_path.chmod(0o600)
            img.save(temp_path, **save_kwargs)
            # Vor einem eventuell sehr restriktiven Endmodus lesbar oeffnen.
            # Der offene Deskriptor versorgt den O_EXCL-Kopierfallback auch
            # dann noch, wenn der Pfad danach nur schreibbar ist.
            source_fd = os.open(temp_path, os.O_RDONLY)
            # mkstemp legt 0600 an; normale Bildausgaben sollen wie bisher für
            # andere lokale Programme lesbar sein — aber nur so weit, wie die
            # umask des Benutzers es zulässt (siehe publish_mode).
            #
            # VOR dem Veröffentlichen setzen: Im Hardlink-Pfad ist die Ausgabe
            # dadurch nie unter ihrem Endnamen mit dem internen 0600 sichtbar.
            # Scheitert chmod, existiert noch kein Endname und finally entfernt
            # die Temp-Datei.
            temp_path.chmod(publish_mode())
            native_suffix = (
                name_suffix
                if src.suffix.lower() in codec.native_extensions
                else ""
            )
            if on_event is not None:
                on_event("publishing", None)
            out_path = publish_unique(
                temp_path,
                base_path,
                source_fd=source_fd,
                name_suffix=native_suffix,
            )
        finally:
            if source_fd is not None:
                os.close(source_fd)
            temp_path.unlink(missing_ok=True)

        result["target"] = str(out_path)
        result["out_bytes"] = out_path.stat().st_size
        result["status"] = "warning" if result["warnings"] else "ok"
    except ImageInputError as exc:
        result["error"] = str(exc)
        result["error_code"] = exc.code
    except (OSError, Image.DecompressionBombError, MemoryError, ValueError, KeyError) as exc:
        # Nur bekannte Bild-/IO-Fehler abfangen — Programmierfehler (AttributeError,
        # TypeError, …) sollen weiterhin laut krachen und nicht stumm als Bildfehler
        # erscheinen. KeyboardInterrupt/SystemExit werden hier bewusst nicht gefangen.
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["error_code"] = "image_or_io_error"
    if on_event is not None:
        on_event("result", result)
    return result


def process_one(
    src: Path,
    codec: Codec,
    preset_name: str,
    name_suffix: str,
    *, on_event: Callable[[str, dict | None], None] | None = None,
) -> dict:
    """Verarbeitet ein einzelnes Bild und liefert auch Fehler strukturiert."""
    return _process_one(src, codec, preset_name, name_suffix, on_event=on_event)


def quality_label(codec_name: str, preset_name: str, quality: int | None) -> str:
    """Kurzer Qualitätsteil der Detailzeile ("q85", "256 Farben", "verlustfrei").

    Eine fehlende Zahl heißt nicht automatisch verlustfrei: das Preset
    ``png:klein_256`` hat keine Qualitätszahl, quantisiert das Bild aber auf
    256 Farben und ist damit sehr wohl verlustbehaftet. Ob quantisiert wird,
    steht am Preset selbst (``extra["palette_colors"]``), damit weitere
    Paletten-Presets automatisch richtig beschriftet werden.
    """
    if quality is not None:
        return f"q{quality}"
    codec = CODECS.get(codec_name)
    if codec is not None:
        for preset in codec.presets:
            if preset.name == preset_name:
                colors = (preset.extra or {}).get("palette_colors")
                if colors:
                    return f"{colors} Farben"
                break
    return "verlustfrei kodiert"


def format_line(r: dict) -> str:
    """Eine lesbare Detailzeile pro Bild für die Kommandozeile."""
    name = Path(r["source"]).name
    if r.get("status") == "skipped":
        return f"  – {name}: {r.get('skip_reason', 'Übersprungen')}"
    if r.get("status") == "cancelled":
        return f"  – {name}: Abgebrochen, keine Ausgabe erzeugt"
    if r["error"]:
        return f"  ✗ {name}: {r['error']}"
    saved = r["src_bytes"] - r["out_bytes"]
    pct = (saved / r["src_bytes"] * 100) if r["src_bytes"] else 0
    arrow = "−" if saved >= 0 else "+"
    # Codec/Preset-Anzeige kompakt: "jpeg:hoch" oder "png:verlustfrei" oder "avif:auto"
    quality_part = quality_label(r["codec"], r["preset"], r["quality"])
    line = (
        f"  ✓ {name} -> {Path(r['target']).name} "
        f"[{r['codec']}:{r['preset']} {r['max_edge']}px {quality_part}] "
        f"{human_size(r['src_bytes'])} -> {human_size(r['out_bytes'])} "
        f"({arrow}{abs(pct):.0f}%)"
    )
    for notice in r.get("warnings", []):
        line += f"\n    ⚠ {notice['message']}"
    return line


def summary_line(results: list[dict]) -> str:
    """Eine kompakte Zusammenfassungszeile (auch für die Dock-Notification)."""
    ok = [r for r in results if not r["error"] and r.get("status") not in {"cancelled", "skipped"}]
    fail = [r for r in results if r["error"]]
    cancelled = sum(r.get("status") == "cancelled" for r in results)
    skipped = sum(r.get("status") == "skipped" for r in results)
    if not ok:
        return (f"0 Bilder konvertiert, {len(fail)} Fehler"
                + (f" · {cancelled} abgebrochen" if cancelled else "")
                + (f" · {skipped} übersprungen" if skipped else ""))
    src_total = sum(r["src_bytes"] for r in ok)
    out_total = sum(r["out_bytes"] for r in ok)
    saved = src_total - out_total
    pct = (saved / src_total * 100) if src_total else 0
    arrow = "−" if saved >= 0 else "+"
    msg = (
        f"{len(ok)} Bild{'er' if len(ok) != 1 else ''} · "
        f"{human_size(src_total)} -> {human_size(out_total)} "
        f"({arrow}{abs(pct):.0f}%)"
    )
    if fail:
        msg += f" · {len(fail)} Fehler"
    if cancelled:
        msg += f" · {cancelled} abgebrochen"
    if skipped:
        msg += f" · {skipped} übersprungen"
    warning_count = sum(len(r.get("warnings", [])) for r in results)
    if warning_count:
        msg += f" · {warning_count} Warnung{'en' if warning_count != 1 else ''}"
    return msg


def batch_message(results: list[dict]) -> str:
    """Gemeinsame Kurzmeldung für CLI-Zusammenfassung und App-Benachrichtigung."""
    message = summary_line(results)
    first_error = next((result for result in results if result.get("error")), None)
    if first_error is not None:
        message += f"\n{Path(first_error['source']).name}: {first_error['error']}"
    first_warning = next((result for result in results if result.get("warnings")), None)
    if first_warning is not None:
        message += f"\n{Path(first_warning['source']).name}: {first_warning['warnings'][0]['message']}"
    return message


# ===========================================================================
# Settings-Persistenz (~/Library/Application Support/JPEGdrop/settings.json)
# Wird von der GUI (und der App) genutzt; CLI liest Defaults daraus, falls
# --codec/--preset nicht gesetzt sind.
# ===========================================================================
SETTINGS_DIR = Path.home() / "Library" / "Application Support" / "JPEGdrop"
SETTINGS_PATH = SETTINGS_DIR / "settings.json"

# Defaults — gelten immer dann, wenn keine (gültige) settings.json existiert;
# geschrieben wird die Datei erst beim ersten Speichern über die GUI.
# Bewusste Wahl seit v2: festes "hoch" (q85) statt der adaptiven v1-Kurve —
# die gibt es weiterhin als Preset "auto".
DEFAULT_SETTINGS = {
    "codec": "jpeg",
    "preset": "hoch",
}


def load_settings() -> dict:
    """Lädt nur ein typfestes, codec-gültiges Settings-Paar."""
    try:
        with SETTINGS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return dict(DEFAULT_SETTINGS)

        codec = data.get("codec")
        if not isinstance(codec, str) or codec not in CODECS:
            return dict(DEFAULT_SETTINGS)

        preset = data.get("preset")
        valid = {p.name for p in CODECS[codec].presets}
        if not isinstance(preset, str) or preset not in valid:
            preset = CODECS[codec].default_preset
        return {"codec": codec, "preset": preset}
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeError):
        return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict) -> None:
    """Schreibt Settings. Legt das Verzeichnis an falls nötig."""
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    # Atomar schreiben: erst .tmp, dann rename
    # codereview-ok: SETTINGS_PATH konstant, .tmp im selben Verzeichnis, os.replace atomar (2026-07-01)
    tmp = SETTINGS_PATH.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS_PATH)


# ===========================================================================
# CLI
# ===========================================================================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jpegdrop",
        description="Erzeugt neben jedem Bild ein platzsparendes, metadatenfreies JPG/PNG/AVIF/WebP.",
    )
    parser.add_argument("--version", action="version", version=f"jpegdrop {__version__}")
    parser.add_argument("paths", nargs="*", help="Bilddateien (HEIC/PNG/JPG/TIFF/WEBP/AVIF ...)")
    parser.add_argument(
        "-c",
        "--codec",
        choices=sorted(CODECS.keys()),
        help="Ziel-Codec (Default: aus Settings, sonst 'jpeg').",
    )
    parser.add_argument(
        "-p",
        "--preset",
        help="Preset-Name innerhalb des Codecs (Default: aus Settings, sonst 'hoch'). "
             "Mit --list-presets ansehen.",
    )
    parser.add_argument(
        "-q",
        "--quality",
        type=int,
        metavar="0-100",
        help="(Legacy) Feste JPEG-Qualität. Wenn gesetzt, wird Codec auf 'jpeg' und "
             "Preset auf 'auto' gezwungen. Besser: --codec jpeg --preset auto.",
    )
    parser.add_argument(
        "--suffix",
        default=DEFAULT_SUFFIX,
        help=f'Namens-Suffix bei gleichem Codec (Standard: "{DEFAULT_SUFFIX}").',
    )
    parser.add_argument(
        "--brief",
        action="store_true",
        help="Nur die Zusammenfassungszeile ausgeben (für die Dock-Notification).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Maschinenlesbarer JSON-Output (für Skripte/Agenten).",
    )
    parser.add_argument(
        "--events", action="store_true",
        help="Fortschritt als JSON-Zeilen (started, image_started, publishing, result, finished).",
    )
    parser.add_argument(
        "--list-presets",
        action="store_true",
        help="Alle Codecs und ihre Presets auflisten, dann beenden.",
    )
    parser.add_argument(
        "--print-settings-path",
        action="store_true",
        help="Pfad zur Settings-Datei ausgeben, dann beenden.",
    )
    return parser


def _sigterm_exit(signum: int, _frame: object) -> None:
    """SIGTERM als normalen Python-Abbruch ausloesen, damit finally aufraeumt."""
    raise SystemExit(128 + signum)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.events and (args.json or args.brief):
        parser.error("--events ist nicht mit --json oder --brief kombinierbar.")

    if args.list_presets:
        print(list_presets_text())
        return 0

    if args.print_settings_path:
        print(SETTINGS_PATH)
        return 0

    if not args.paths:
        parser.error("Keine Bilddateien angegeben. Tipp: --list-presets zeigt die Optionen.")

    # Settings laden, Defaults drüberlegen
    settings = load_settings()

    # --quality (Legacy) hat Vorrang und erzwingt JPEG + Auto-Preset.
    if args.quality is not None:
        # Widersprüchliche Flags sauber ablehnen statt still ignorieren:
        # -q erzwingt JPEG mit fester Qualität — ein --codec avif oder
        # --preset daneben würde sonst kommentarlos untergehen.
        if args.codec not in (None, "jpeg"):
            parser.error("--quality erzwingt JPEG — nicht mit --codec kombinierbar.")
        if args.preset is not None:
            parser.error("--quality und --preset schließen sich aus (feste Qualität ODER Preset).")
        if not (0 <= args.quality <= 100):
            parser.error("--quality muss zwischen 0 und 100 liegen.")
        codec = get_codec("jpeg")
        # Legacy-Pfad: feste Qualität über process_one_with_fixed_quality,
        # nicht über die normale Preset-Logik.
        preset_name = "auto"
    else:
        codec = get_codec(args.codec or settings["codec"])
        if args.preset is not None:
            preset_name = args.preset
        elif args.codec is None:
            preset_name = settings["preset"]
        else:
            # Ein expliziter Codec darf das gespeicherte Preset nur übernehmen,
            # wenn dieses auch dort existiert. Beispiel: jpeg/hoch -> PNG nutzt
            # verlustfrei statt mit einem Preset-Fehler abzubrechen.
            valid = {p.name for p in codec.presets}
            preset_name = (
                settings["preset"]
                if settings["preset"] in valid
                else codec.default_preset
            )
        # Preset-Existenz prüfen (gibt auch saubere Fehlermeldung)
        get_preset(codec, preset_name)

    suffix_bytes = len(args.suffix.encode("utf-8", "surrogateescape"))
    extension_bytes = len(codec.extension.encode("utf-8"))
    min_collision_bytes = len("-2")
    # Bei einem Wechsel des Codecs verwendet target_base_path das Suffix gar
    # nicht. Nur native Eingaben brauchen deshalb das Namensbudget dafuer.
    suffix_is_used = any(
        Path(path).suffix.lower() in codec.native_extensions for path in args.paths
    )
    if suffix_is_used and suffix_bytes + extension_bytes + min_collision_bytes >= NAME_MAX:
        parser.error(
            f"--suffix ist zu lang: Suffix, Endung und Kollisionszähler müssen zusammen unter "
            f"{NAME_MAX} Bytes bleiben."
        )

    # Verarbeitung. SIGTERM (unter anderem vom Installer) wird in SystemExit
    # umgewandelt: So laufen die finally-Bloecke und entfernen die gerade
    # geschriebene .jpegdrop-Zwischendatei, bevor der Prozess endet.
    results: list[dict] = []
    publishing = False
    pending_term = None

    def request_stop(signum, frame):
        nonlocal pending_term
        if publishing:
            pending_term = signum
        else:
            _sigterm_exit(signum, frame)

    def image_event(kind: str, result: dict | None) -> None:
        nonlocal publishing
        if kind == "result":
            results.append(result)
            emit(kind, index=index, total=len(args.paths), result=result)
            publishing = False
            if pending_term is not None:
                _sigterm_exit(pending_term, None)
        else:
            # Eine veröffentlichte Datei muss der GUI noch bestätigt werden.
            # Erst nach diesem Ergebnis darf SIGTERM die Verarbeitung beenden.
            publishing = True
            emit(kind, index=index, total=len(args.paths))

    def emit(event: str, **fields) -> None:
        if args.events:
            # ASCII-Escapes erhalten auch nicht als UTF-8 dekodierbare Dateinamen.
            print(json.dumps({"event": event, **fields}, ensure_ascii=True), flush=True)

    previous_sigterm = signal.signal(signal.SIGTERM, request_stop)
    try:
        emit("started", total=len(args.paths))
        for index, p in enumerate(args.paths):
            emit("image_started", index=index, total=len(args.paths), source=p)
            if args.quality is not None:
                r = process_one_with_fixed_quality(Path(p), args.quality, args.suffix,
                                                   on_event=image_event if args.events else None)
            else:
                r = process_one(Path(p), codec, preset_name, args.suffix,
                                on_event=image_event if args.events else None)
            if not args.events:
                results.append(r)
        emit("finished", total=len(args.paths), completed=len(results))
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)

    if args.events:
        pass
    elif args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    elif args.brief:
        print(summary_line(results))
    else:
        for r in results:
            print(format_line(r))
        print(summary_line(results))

    # Exit-Code: Anzahl Fehler (0 = alles ok) -> gut für Skripte/Droplet.
    failures = sum(1 for r in results if r["error"])
    return min(failures, 125)


def process_one_with_fixed_quality(
    src: Path, quality: int, name_suffix: str,
    *, on_event: Callable[[str, dict | None], None] | None = None,
) -> dict:
    """Legacy: --quality N ist äquivalent zu --codec jpeg mit fester q.
    Bewahrt die alte -q/--quality Semantik (eine q für alle Bilder)."""
    jpeg_codec = get_codec("jpeg")
    return _process_one(
        src,
        jpeg_codec,
        f"fixed-q{quality}",
        name_suffix,
        fixed_quality=quality,
        on_event=on_event,
    )


if __name__ == "__main__":
    sys.exit(main())
