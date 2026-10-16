"""Echte Bild-/CLI-Proben für Inhaltsverlust, Farbsicherheit und Rückmeldungen."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jpegdrop
from PIL import Image, ImageCms


class InputContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="jpegdrop-input-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def convert(self, source, codec="png"):
        target_codec = jpegdrop.get_codec(codec)
        return jpegdrop.process_one(source, target_codec, target_codec.default_preset, "-klein")

    def test_multiple_frames_are_rejected_by_content_without_touching_source(self):
        for fmt in ("GIF", "PNG", "WEBP", "TIFF"):
            with self.subTest(fmt=fmt):
                source = self.root / f"{fmt}.jpg"
                frames = [Image.new("RGB", (24, 24), color) for color in ("red", "blue")]
                frames[0].save(source, format=fmt, save_all=True, append_images=frames[1:])
                before = hashlib.sha256(source.read_bytes()).digest()
                result = self.convert(source)
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["error_code"], "multiple_images")
                self.assertIn("2", result["error"])
                self.assertIsNone(result["target"])
                self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), before)
                self.assertFalse(list(self.root.glob(".jpegdrop-*")))

    def test_static_images_still_convert_and_strip_metadata(self):
        for fmt in ("GIF", "PNG", "WEBP", "TIFF", "JPEG"):
            with self.subTest(fmt=fmt):
                source = self.root / f"static-{fmt}.image"
                Image.new("RGB", (24, 16), "orange").save(source, format=fmt)
                original = source.read_bytes()
                result = self.convert(source)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["warnings"], [])
                self.assertIsNone(result["error_code"])
                self.assertEqual(source.read_bytes(), original)
                with Image.open(result["target"]) as output:
                    self.assertEqual(output.size, (24, 16))
                    self.assertFalse(output.getexif())
                    self.assertNotIn("icc_profile", output.info)

    def test_invalid_profile_produces_no_output(self):
        for fmt in ("PNG", "JPEG"):
            with self.subTest(fmt=fmt):
                source = self.root / f"invalid-{fmt}.image"
                Image.new("RGB", (16, 16), "red").save(source, format=fmt, icc_profile=b"invalid ICC profile")
                original = source.read_bytes()
                result = self.convert(source)
                self.assertEqual(result["error_code"], "invalid_color_profile")
                self.assertIn("Farbprofil", result["error"])
                self.assertIsNone(result["target"])
                self.assertEqual(source.read_bytes(), original)

    def test_incompatible_profile_is_not_silently_discarded(self):
        source = self.root / "cmyk.jpg"
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        Image.new("CMYK", (16, 16), (0, 120, 20, 0)).save(source, icc_profile=profile)
        result = self.convert(source)
        self.assertEqual(result["error_code"], "invalid_color_profile")
        self.assertIsNone(result["target"])

    def test_present_but_empty_or_non_binary_profile_is_rejected(self):
        for profile in (b"", "not binary"):
            with self.subTest(profile=profile):
                image = Image.new("RGB", (16, 16), "orange")
                image.info["icc_profile"] = profile
                with self.assertRaises(jpegdrop.ImageInputError):
                    jpegdrop.normalize_image(image)

    def test_transparency_warning_is_visible_in_every_output_codec(self):
        source = self.root / "transparent.png"
        Image.new("RGBA", (16, 16), (255, 0, 0, 80)).save(source)
        for codec in jpegdrop.CODECS:
            with self.subTest(codec=codec):
                result = self.convert(source, codec)
                self.assertEqual(result["status"], "warning")
                self.assertIsNone(result["error"])
                self.assertIsNone(result["error_code"])
                self.assertEqual(result["warnings"][0]["code"], "transparency_flattened")
                self.assertIn("Transparenz", jpegdrop.format_line(result))
                self.assertIn("Warnung", jpegdrop.summary_line([result]))
                self.assertIn("transparent.png", jpegdrop.batch_message([result]))
                with Image.open(result["target"]) as output:
                    self.assertNotIn("A", output.getbands())
                    self.assertFalse(output.getexif())
                    self.assertNotIn("icc_profile", output.info)

    def test_opaque_alpha_does_not_claim_transparency_was_lost(self):
        source = self.root / "opaque.png"
        Image.new("RGBA", (16, 16), (255, 0, 0, 255)).save(source)
        result = self.convert(source)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["warnings"], [])

    def test_cli_json_preserves_partial_success_and_error_count(self):
        good = self.root / "good.png"
        bad = self.root / "bad.png"
        Image.new("RGBA", (16, 16), (255, 0, 0, 80)).save(good)
        bad.write_bytes(b"not an image")
        proc = subprocess.run(
            [sys.executable, "-B", str(Path(jpegdrop.__file__)), "--codec", "png", "--json", str(good), str(bad)],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(proc.returncode, 1)
        results = json.loads(proc.stdout)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["status"], "warning")
        self.assertEqual(results[1]["status"], "error")
        self.assertIsNotNone(results[0]["target"])
        self.assertIsNone(results[1]["target"])
        self.assertIn("bad.png", jpegdrop.batch_message(results))
        self.assertIn("1 Fehler", jpegdrop.batch_message(results))

    def test_warnings_do_not_change_success_exit_code(self):
        source = self.root / "transparent.png"
        Image.new("RGBA", (16, 16), (255, 0, 0, 80)).save(source)
        proc = subprocess.run(
            [sys.executable, "-B", str(Path(jpegdrop.__file__)), "--codec", "png", "--brief", str(source)],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Warnung", proc.stdout)


if __name__ == "__main__":
    unittest.main()
