"""Ordnerinventur, Zielstruktur und Quellschutz an tatsächlichen Dateien prüfen."""
from pathlib import Path
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jpegdrop as jd
from app.input_plan import InventoryOptions, InventoryCancelled, inventory_inputs
from PIL import Image


class InputPlanTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.source = self.root / "source"
        self.source.mkdir()

    def tearDown(self):
        self.directory.cleanup()

    def image(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (24, 12), (20, 40, 60, 100)).save(path)
        return path

    def test_optional_recursion_deduplication_links_and_nonimages(self):
        first = self.image(self.source / "a.png")
        second = self.image(self.source / "nested/b.png")
        (self.source / "note.txt").write_text("keep")
        (self.source / "loop").symlink_to(self.source, target_is_directory=True)
        (self.source / "linked.png").symlink_to(first)
        os.link(first, self.source / "hardlink.png")
        inventory = inventory_inputs([str(self.source)], InventoryOptions())
        self.assertEqual([r.source for r in inventory.entries], [str(first)])
        self.assertEqual(len(inventory.issues), 5)
        inventory = inventory_inputs([str(self.source), str(first), str(self.source)], InventoryOptions(recursive=True))
        self.assertEqual({r.source for r in inventory.entries}, {str(first), str(second)})
        self.assertTrue(any("Doppelten Ordner" in r.message for r in inventory.issues))
        self.assertTrue(any("Symbolische" in r.message for r in inventory.issues))

    def test_target_inside_source_is_excluded_and_structure_is_retained(self):
        first = self.image(self.source / "a.png")
        second = self.image(self.source / "nested/a.png")
        output = self.source / "out"
        old = self.image(output / "old.png")
        options = InventoryOptions(True, "directory", str(output))
        inventory = inventory_inputs([str(self.source)], options)
        self.assertEqual({r.source for r in inventory.entries}, {str(first), str(second)})
        self.assertEqual({r.output_directory for r in inventory.entries},
                         {str(output / "source"), str(output / "source/nested")})
        self.assertTrue(old.exists())

    def test_target_alias_inside_source_is_also_excluded(self):
        first = self.image(self.source / "a.png")
        target = self.source / "out"; self.image(target / "previous.png")
        alias = self.root / "output-link"; alias.symlink_to(target, target_is_directory=True)
        inventory = inventory_inputs([str(self.source)], InventoryOptions(True, "directory", str(alias)))
        self.assertEqual([entry.source for entry in inventory.entries], [str(first)])

    def test_read_failure_and_cancellation_remain_visible_without_output(self):
        with patch('app.input_plan.os.scandir', side_effect=PermissionError('controlled read failure')):
            inventory = inventory_inputs([str(self.source)], InventoryOptions(True, 'subfolder'))
        self.assertFalse(inventory.entries)
        self.assertTrue(inventory.issues[0].error)
        for index in range(10):
            self.image(self.source / f'{index}.png')
        count = 0
        def cancelled():
            nonlocal count
            count += 1
            return count > 5
        with self.assertRaises(InventoryCancelled):
            inventory_inputs([str(self.source)], InventoryOptions(True, 'subfolder'), cancelled=cancelled)
        self.assertFalse((self.source/'JPEGdrop').exists())

    def test_subfolder_and_target_equal_source_keep_inputs(self):
        first = self.image(self.source / "a.png")
        second = self.image(self.source / "nested/b.png")
        self.image(self.source / "JPEGdrop/previous.png")
        inventory = inventory_inputs([str(self.source)], InventoryOptions(True, "subfolder"))
        self.assertEqual({r.source for r in inventory.entries}, {str(first), str(second)})
        self.assertEqual(inventory.entries[1].output_directory, str(self.source / "JPEGdrop/nested"))
        inventory = inventory_inputs([str(self.source)], InventoryOptions(True, "directory", str(self.source)))
        self.assertIn(str(first), {r.source for r in inventory.entries})

    def test_inventory_is_fixed_before_new_outputs_and_bounds_write_nothing(self):
        first = self.image(self.source / "a.png")
        second = self.image(self.source / "b.png")
        options = InventoryOptions(True, "subfolder")
        inventory = inventory_inputs([str(self.source)], options)
        self.image(self.source / "later.png")
        self.assertEqual({r.source for r in inventory.entries}, {str(first), str(second)})
        with self.assertRaisesRegex(ValueError, "2 Bilder"):
            inventory_inputs([str(self.source)], options, max_images=2)
        self.assertFalse((self.source / "JPEGdrop").exists())
        with self.assertRaisesRegex(ValueError, "Einträge"):
            inventory_inputs([str(self.source)], options, max_entries=2)
        with self.assertRaises(InventoryCancelled):
            inventory_inputs([str(self.source)], options, cancelled=lambda: True)

    def test_packages_and_explicit_symlinks_are_not_traversed(self):
        self.image(self.source / "program.app/inside.png")
        original = self.image(self.source / "normal.png")
        link = self.root / "link.png"; link.symlink_to(original)
        inventory = inventory_inputs([str(self.source), str(link)], InventoryOptions(recursive=True))
        self.assertEqual([r.source for r in inventory.entries], [str(original)])
        self.assertEqual(len(inventory.issues), 2)

    def test_cli_nested_outputs_and_collisions_preserve_sources_and_foreign_files(self):
        first = self.image(self.source / "a.png")
        second = self.image(self.source / "nested/a.png")
        output = self.root / "output"
        occupied = output / "source/a.jpg"; occupied.parent.mkdir(parents=True)
        occupied.write_bytes(b"foreign file")
        hashes = {p: hashlib.sha256(p.read_bytes()).digest() for p in (first, second)}
        settings = self.root / "settings.json"
        with patch.object(jd, "SETTINGS_PATH", settings), contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(jd.main(["--json", "--recursive", "--codec", "jpeg", "--output-dir", str(output), str(self.source)]), 0)
        rows = json.loads(stdout.getvalue())
        self.assertEqual({r["target"] for r in rows}, {str(output / "source/a-2.jpg"), str(output / "source/nested/a.jpg")})
        self.assertEqual(occupied.read_bytes(), b"foreign file")
        self.assertEqual({p: hashlib.sha256(p.read_bytes()).digest() for p in hashes}, hashes)
        for row in rows:
            with Image.open(row["target"]) as image:
                self.assertEqual(image.size, (24, 12)); self.assertFalse(image.getexif())

    def test_unwritable_destination_and_existing_nondirectory_are_reported(self):
        source = self.image(self.source / "a.png")
        output = self.root / "blocked"; output.mkdir(); output.chmod(0o500)
        try:
            result = jd.process_one(source, jd.CODECS["png"], "verlustfrei", "-out", output_directory=output)
            self.assertIsNotNone(result["error"])
            self.assertEqual(list(output.iterdir()), [])
        finally:
            output.chmod(0o700)
        file = self.root / "existing"; file.write_bytes(b"foreign")
        result = jd.process_one(source, jd.CODECS["png"], "verlustfrei", "-out", output_directory=file)
        self.assertIsNotNone(result["error"])
        self.assertEqual(file.read_bytes(), b"foreign")

    def check_manifest(self, source):
        output = self.root / "out"
        plan = self.root / "inputs.json"
        plan.write_text(json.dumps([{"source": str(source), "output_directory": str(output)}], ensure_ascii=True))
        result = subprocess.run([sys.executable, '-I', '-B', jd.__file__, '--codec', 'png', '--preset', 'verlustfrei',
                                 '--max-edge', '0', '--flatten-alpha', '--json', '--input-plan', str(plan)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)[0]
        self.assertTrue(Path(row['target']).is_file())
        self.assertLessEqual(len(os.fsencode(Path(row['target']).name)), 255)
        self.assertEqual(row['source'], str(source))

    def test_long_filename_works_via_manifest_in_isolated_cli(self):
        source = self.image(self.source / ('ä' * 120 + 'x' * 11 + '.png'))
        self.check_manifest(source)

    def test_raw_filename_where_filesystem_supports_it(self):
        source = Path(os.fsdecode(os.fsencode(self.source) + b'/raw-\xff.png'))
        try:
            self.image(source)
        except OSError as error:
            if error.errno == 92:
                self.skipTest('Dateisystem lehnt Nicht-UTF-8-Namen ab')
            raise
        self.check_manifest(source)


if __name__ == "__main__":
    unittest.main()
