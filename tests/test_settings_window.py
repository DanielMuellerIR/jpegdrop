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
    def test_image_options_validation_cancel_and_codec_state(self):
        A.NSApplication.sharedApplication().setActivationPolicy_(A.NSApplicationActivationPolicyProhibited)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(jpegdrop, "SETTINGS_DIR", Path(directory)), \
                patch.object(jpegdrop, "SETTINGS_PATH", Path(directory) / "settings.json"):
            saved = {**jpegdrop.DEFAULT_SETTINGS, "codec": "webp", "preset": "verlustfrei",
                     "max_edge": 1200, "preserve_alpha": True, "background": "#123456"}
            jpegdrop.save_settings(saved)
            original = jpegdrop.SETTINGS_PATH.read_bytes()
            controller = SettingsWindowController.alloc().init()
            try:
                controller._populate_from_settings()
                self.assertEqual(controller._max_edge.stringValue(), "1200")
                self.assertTrue(controller._preserve_alpha.isEnabled())
                self.assertFalse(controller._background.isEnabled())
                controller._max_edge.setStringValue_("-3")
                controller.saveAndClose_(None)
                self.assertFalse(controller._validation_error.isHidden())
                self.assertEqual(jpegdrop.SETTINGS_PATH.read_bytes(), original)
                controller._max_edge.setStringValue_("300")
                controller.close_(None)
                controller._populate_from_settings()
                self.assertEqual(controller._max_edge.stringValue(), "1200")
                controller._max_edge.setStringValue_("300")
                controller.saveAndClose_(None)
                self.assertEqual(jpegdrop.load_settings(), {**saved, "max_edge": 300})
                controller._populate_from_settings()
                controller._codec_popup.selectItemAtIndex_(controller._codec_names.index("jpeg"))
                controller.codecChanged_(None)
                self.assertFalse(controller._preserve_alpha.isEnabled())
                self.assertEqual(controller._preserve_alpha.state(), A.NSControlStateValueOff)
                self.assertTrue(controller._background.isEnabled())
                controller.saveAndClose_(None)
                self.assertFalse(jpegdrop.load_settings()["preserve_alpha"])
                self.assertEqual(jpegdrop.load_settings()["background"], "#123456")
            finally:
                controller._window.close()

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
                self.assertEqual(jpegdrop.load_settings(), dict(jpegdrop.DEFAULT_SETTINGS))
                updates.available = False
                controller._populate_from_settings()
                self.assertFalse(controller._automatic_checks.isEnabled())
                self.assertEqual(controller._update_info.stringValue(), updates.problem)
            finally:
                controller._window.close()


if __name__ == "__main__":
    unittest.main()
