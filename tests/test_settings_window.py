"""Update-Schalter nur beim Speichern übernehmen, fehlenden Updater erklären."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import AppKit as A
import jpegdrop
from app.menu_app import SettingsWindowController


class SettingsWindowTests(unittest.TestCase):
    def test_update_preference_save_cancel_and_unavailable_state(self):
        A.NSApplication.sharedApplication().setActivationPolicy_(A.NSApplicationActivationPolicyProhibited)
        class Updates:
            available = True
            automatic_checks = True
            problem = "Updates benötigen die gebaute JPEGdrop-App."
            def set_automatic_checks(self, enabled):
                self.automatic_checks = enabled
        updates = Updates()
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(jpegdrop, "SETTINGS_DIR", Path(directory)), \
                patch.object(jpegdrop, "SETTINGS_PATH", Path(directory) / "settings.json"):
            controller = SettingsWindowController.alloc().init()
            controller._update_controller = updates
            try:
                controller._populate_from_settings()
                self.assertTrue(controller._automatic_checks.isEnabled())
                self.assertEqual(controller._automatic_checks.state(), A.NSControlStateValueOn)
                controller._automatic_checks.setState_(A.NSControlStateValueOff)
                controller.close_(None)
                self.assertTrue(updates.automatic_checks)
                controller._populate_from_settings()
                self.assertEqual(controller._automatic_checks.state(), A.NSControlStateValueOn)
                controller._automatic_checks.setState_(A.NSControlStateValueOff)
                controller.saveAndClose_(None)
                self.assertFalse(updates.automatic_checks)
                self.assertEqual(jpegdrop.load_settings(), {"codec": "jpeg", "preset": "hoch"})
                updates.available = False
                controller._populate_from_settings()
                self.assertFalse(controller._automatic_checks.isEnabled())
                self.assertEqual(controller._update_info.stringValue(), updates.problem)
            finally:
                controller._window.close()


if __name__ == "__main__":
    unittest.main()
