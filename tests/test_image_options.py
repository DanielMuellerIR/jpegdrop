"""Bildoptionen an tatsächlichen Ausgaben, Pixeln und unveränderten Quellen prüfen."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jpegdrop as jd
from PIL import Image, ImageCms


class ImageOptionsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def convert(self, image, codec="png", preset="verlustfrei", options=jd.ImageOptions(), **save):
        source = self.root / "source.png"
        image.save(source, **save)
        before = hashlib.sha256(source.read_bytes()).digest()
        result = jd.process_one(source, jd.get_codec(codec), preset, "-out", options=options)
        self.assertIsNone(result["error"], result)
        self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), before)
        with Image.open(result["target"]) as output:
            self.assertFalse(output.getexif())
            self.assertFalse(output.info.get("icc_profile"))
            self.assertFalse(output.info.get("xmp"))
            return result, output.copy()

    def test_orientation_resize_no_upscale_and_auto_quality(self):
        exif = Image.Exif(); exif[274] = 6
        result, output = self.convert(Image.new("RGB", (3000, 1500), "red"), "jpeg", "auto",
                                      jd.ImageOptions(max_edge=600), exif=exif)
        self.assertEqual((result["source_width"], result["source_height"]), (1500, 3000))
        self.assertEqual(output.size, (300, 600))
        self.assertTrue(result["resized"])
        self.assertEqual(result["quality"], 90)
        result, output = self.convert(Image.new("RGB", (12, 7), "red"), options=jd.ImageOptions(max_edge=600))
        self.assertEqual(output.size, (12, 7))
        self.assertFalse(result["resized"])

    def test_transparency_preserved_by_all_alpha_codecs_and_palette(self):
        source = Image.new("RGBA", (16, 16), (50, 100, 150, 64))
        for codec, preset in [("png", "verlustfrei"), ("png", "klein_256"),
                              ("webp", "verlustfrei"), ("avif", "hoch")]:
            with self.subTest(codec=codec, preset=preset):
                result, output = self.convert(source, codec, preset, jd.ImageOptions(preserve_alpha=True))
                self.assertTrue(result["alpha_preserved"])
                self.assertFalse(result["warnings"])
                self.assertEqual(output.convert("RGBA").getchannel("A").getextrema(), (64, 64))

    def test_lossless_webp_preserves_prepared_rgba_including_hidden_colors(self):
        source = Image.new("RGBA", (32, 8))
        source.putdata([(x * 7 % 256, x * 11 % 256, x * 17 % 256, (x % 4) * 85) for x in range(256)])
        result, output = self.convert(source, "webp", "verlustfrei", jd.ImageOptions(preserve_alpha=True))
        self.assertEqual(output.convert("RGBA").tobytes(), source.tobytes())
        self.assertIsNone(result["quality"])
        self.assertEqual(jd.quality_label("webp", "verlustfrei", result["quality"]), "verlustfrei kodiert")

    def test_png_transparent_keys_become_preserved_alpha(self):
        palette = Image.new("P", (8, 6), 1)
        palette.putpalette([0, 0, 0, 10, 20, 30] + [0] * 762)
        for source, transparency in [(palette, 1), (Image.new("L", (8, 6), 100), 100),
                                     (Image.new("RGB", (8, 6), (10, 20, 30)), (10, 20, 30))]:
            with self.subTest(mode=source.mode):
                result, output = self.convert(source, options=jd.ImageOptions(preserve_alpha=True),
                                              transparency=transparency)
                self.assertEqual(output.mode, "RGBA")
                self.assertEqual(output.getchannel("A").getextrema(), (0, 0))
                self.assertTrue(result["alpha_preserved"])

    def test_custom_background_and_jpeg_alpha_rejection(self):
        source = Image.new("RGBA", (12, 8), (200, 0, 0, 0))
        result, output = self.convert(source, options=jd.ImageOptions(background="#123456"))
        self.assertEqual(output.getpixel((0, 0)), (18, 52, 86))
        self.assertEqual(result["warnings"][0]["code"], "transparency_flattened")
        self.assertIn("#123456", result["warnings"][0]["message"])
        invalid = jd.process_one(self.root / "source.png", jd.get_codec("jpeg"), "hoch", "-bad",
                                 options=jd.ImageOptions(preserve_alpha=True))
        self.assertEqual(invalid["error_code"], "unsupported_alpha")
        self.assertIsNone(invalid["target"])

    def test_gray_profile_and_alpha_are_transformed_separately(self):
        profile = Path("/System/Library/ColorSync/Profiles/Generic Gray Gamma 2.2 Profile.icc")
        if not profile.exists():
            self.skipTest("System-Grauprofil fehlt")
        gray = Image.new("LA", (12, 8), (100, 64))
        result, output = self.convert(gray, options=jd.ImageOptions(preserve_alpha=True), icc_profile=profile.read_bytes())
        expected = ImageCms.profileToProfile(gray.getchannel("L"), str(profile), ImageCms.createProfile("sRGB"), outputMode="RGB")
        expected.putalpha(gray.getchannel("A"))
        self.assertEqual(output.tobytes(), expected.tobytes())
        self.assertTrue(result["alpha_preserved"])

    def test_old_settings_are_read_without_write_and_invalid_fields_fall_back(self):
        path = self.root / "settings.json"
        original = b'{"codec":"webp","preset":"hoch"}'
        path.write_bytes(original)
        with patch.object(jd, "SETTINGS_PATH", path):
            self.assertEqual(jd.load_settings(), {**jd.DEFAULT_SETTINGS, "codec": "webp"})
            self.assertEqual(path.read_bytes(), original)
            path.write_text(json.dumps({"codec": "png", "max_edge": True, "preserve_alpha": "yes", "background": "#aabbcc"}))
            self.assertEqual(jd.load_settings(), {**jd.DEFAULT_SETTINGS, "codec": "png", "preset": "verlustfrei", "background": "#AABBCC"})

    def test_cli_overrides_saved_options_and_rejects_invalid_input(self):
        source = self.root / "input.png"; Image.new("RGBA", (12, 8), (1, 2, 3, 64)).save(source)
        settings = self.root / "settings.json"
        settings.write_text(json.dumps({"codec": "png", "preset": "verlustfrei", "max_edge": 4,
                                        "preserve_alpha": True, "background": "#000000"}))
        with patch.object(jd, "SETTINGS_PATH", settings):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(jd.main(["--json", "--max-edge", "0", "--flatten-alpha", str(source)]), 0)
            result = json.loads(output.getvalue())[0]
            self.assertEqual((result["output_width"], result["output_height"]), (12, 8))
            self.assertFalse(result["alpha_preserved"])
            for flags in [["--max-edge", "-1"], ["--background", "#abc"], ["--codec", "jpeg", "--preserve-alpha"]]:
                with self.subTest(flags=flags), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                    jd.main([*flags, str(source)])
                self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
