"""Headless-Regressionen des API-Reviews mit synthetischen Bilddateien."""
import hashlib
import struct
import zlib

from PIL import Image
import pytest

import jpegdrop


def png_chunk(kind, payload):
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))


@pytest.mark.parametrize("preserve", [True, False])
@pytest.mark.parametrize("transparent", [True, False])
def test_16bit_png_transparency_key(tmp_path, preserve, transparent):
    source = tmp_path / "gray16.png"
    # Unabhängig vom Pillow-Encoder: 16-Bit-Graustufen-PNG mit tRNS=1000.
    source.write_bytes(b"\x89PNG\r\n\x1a\n" +
        png_chunk(b"IHDR", struct.pack(">IIBBBBB", 5, 1, 16, 0, 0, 0, 0)) +
        (png_chunk(b"tRNS", struct.pack(">H", 1000)) if transparent else b"") +
        png_chunk(b"tEXt", b"Comment\0private fixture metadata") +
        png_chunk(b"IDAT", zlib.compress(b"\0" + struct.pack(">5H", 1000, 2000, 0, 32768, 65535))) +
        png_chunk(b"IEND", b""))
    with Image.open(source) as original:
        assert original.info.get("transparency") == (1000 if transparent else None)
        assert original.getpixel((0, 0)) == 1000
        assert original.getpixel((1, 0)) == 2000
    before = hashlib.sha256(source.read_bytes()).digest()
    result = jpegdrop.process_one(source, jpegdrop.get_codec("png"), "verlustfrei", "-out",
        options=jpegdrop.ImageOptions(preserve_alpha=preserve, background="#123456"))
    assert result["error"] is None, result
    assert hashlib.sha256(source.read_bytes()).digest() == before
    with Image.open(result["target"]) as output:
        assert output.size == (5, 1)
        assert not output.getexif()
        assert not output.info.get("icc_profile")
        assert "transparency" not in output.info
        assert "Comment" not in output.info
        assert output.getpixel((1, 0))[:3] == (8, 8, 8)
        for x, value in [(2, 0), (3, 128), (4, 255)]:
            assert output.getpixel((x, 0))[:3] == (value, value, value)
        if preserve and transparent:
            assert output.getchannel("A").tobytes() == b"\x00\xff\xff\xff\xff"
            assert result["alpha_preserved"]
        elif transparent:
            assert output.getpixel((0, 0)) == (18, 52, 86)
            assert result["warnings"][0]["code"] == "transparency_flattened"
        else:
            assert output.getpixel((0, 0))[:3] == (4, 4, 4)
