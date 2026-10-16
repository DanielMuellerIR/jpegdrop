"""Retina-Raster und Schutz vorhandener Layoutdaten ohne GUI-Fokus prüfen."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import AppKit as A
from PIL import Image
from tools.dmg_layout import write_layout
from tools.render_dmg_background import render


class DMGLayoutTests(unittest.TestCase):
    def test_retina_and_normal_representation_show_the_same_four_quadrants(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "pattern.svg", root / "pattern.tiff"
            source.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40">'
                '<rect width="20" height="20" fill="red"/>'
                '<rect x="20" width="20" height="20" fill="lime"/>'
                '<rect y="20" width="20" height="20" fill="blue"/>'
                '<rect x="20" y="20" width="20" height="20" fill="white"/></svg>')
            render(source, output)
            image = A.NSImage.alloc().initWithContentsOfFile_(str(output))
            self.assertEqual(len(image.representations()), 2)
            self.assertTrue(all(rep.size().width == 40 and rep.size().height == 40 for rep in image.representations()))
            with Image.open(output) as raster:
                self.assertEqual(raster.n_frames, 2)
                sizes = set()
                for index in range(raster.n_frames):
                    raster.seek(index)
                    sizes.add(raster.size)
                    scale = raster.width // 40
                    rgb = raster.convert("RGB")
                    red, green, blue, white = [rgb.getpixel((x * scale, y * scale))
                                               for x, y in ((5, 5), (25, 5), (5, 25), (25, 25))]
                    self.assertGreater(red[0], 180); self.assertLess(max(red[1:]), 80)
                    self.assertGreater(green[1], 180); self.assertLess(max(green[0], green[2]), 80)
                    self.assertGreater(blue[2], 180); self.assertLess(max(blue[:2]), 80)
                    self.assertGreater(min(white), 180)
                self.assertEqual(sizes, {(40, 40), (80, 80)})

    def test_nonmounted_and_existing_layout_targets_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "JPEGdrop.app").mkdir()
            with self.assertRaises(ValueError):
                write_layout(root, root / "absent.tiff")
            marker = root / ".DS_Store"
            marker.write_bytes(b"existing layout")
            with patch("tools.dmg_layout.os.path.ismount", return_value=True), self.assertRaises(ValueError):
                write_layout(root, root / "absent.tiff")
            self.assertEqual(marker.read_bytes(), b"existing layout")
            self.assertFalse((root / ".background").exists())


if __name__ == "__main__":
    unittest.main()
