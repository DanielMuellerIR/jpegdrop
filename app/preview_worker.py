"""Isolierter Vorschauprozess; ausschließlich die gemeinsame Encoderpipeline."""
from pathlib import Path
import json
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jpegdrop
from PIL import Image

MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_SOURCE_PIXELS = 64_000_000


def source_signature(path: Path) -> tuple[int, ...]:
    if path.is_symlink():
        raise ValueError("Symbolische Links werden wie bei regulären Aufträgen übersprungen. Bitte die Originaldatei wählen.")
    info = path.stat()
    if not path.is_file():
        raise ValueError("Bitte eine einzelne Bilddatei wählen.")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def render(source: Path, settings: dict, directory: Path) -> dict:
    signature = source_signature(source)
    if signature[2] > MAX_SOURCE_BYTES:
        raise ValueError("Die Vorschau unterstützt Quelldateien bis 256 MiB.")
    # Beide Ansichten und der Encoder lesen dieselbe private Quellkopie.
    local = directory / ("source" + source.suffix)
    shutil.copyfile(source, local)
    if source_signature(source) != signature:
        raise ValueError("Die Quelle wurde verändert. Bitte erneut eine Vorschau berechnen.")
    with Image.open(local) as opened:
        if opened.width * opened.height > MAX_SOURCE_PIXELS:
            raise ValueError("Die Vorschau unterstützt Bilder bis 64 Megapixel.")
    codec = jpegdrop.get_codec(settings["codec"])
    result = jpegdrop.process_one(local, codec, settings["preset"], "-klein",
        options=jpegdrop.ImageOptions.from_settings(settings), output_directory=directory / "encoded")
    result["source"] = str(source)
    if result["error"]:
        return {"result": result, "signature": signature}
    with Image.open(local) as opened:
        before = jpegdrop.normalize_image(opened, options=jpegdrop.ImageOptions(preserve_alpha=True))
        before.save(directory / "before.png", compress_level=1)
    with Image.open(result["target"]) as opened:
        opened.load()
        opened.save(directory / "after.png", format="PNG", compress_level=1)
    local.unlink()
    return {"result": result, "signature": signature,
            "before": str(directory / "before.png"), "after": str(directory / "after.png")}


if __name__ == "__main__":
    directory = Path(sys.argv[1])
    try:
        request = json.loads((directory / "request.json").read_text())
        response = render(Path(request["source"]), request["settings"], directory)
    except (OSError, ValueError, Image.DecompressionBombError, MemoryError) as error:
        response = {"error": str(error)}
    (directory / "response.json").write_text(json.dumps(response))
