"""Native Controls: Auswahl, Ergebnisgründe und verfügbare Aktionen ohne GUI-Fokus."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import AppKit as A
import Foundation as F
import jpegdrop
from app.jobs import Job
from app.jobs_window import JobsWindowController


class JobsWindowTests(unittest.TestCase):
    def test_native_selection_buttons_and_full_result_details(self):
        A.NSApplication.sharedApplication().setActivationPolicy_(A.NSApplicationActivationPolicyProhibited)
        with tempfile.TemporaryDirectory(prefix="jpegdrop_window_") as directory:
            root = Path(directory)
            output = root / "erzeugt.jpg"; output.write_bytes(b"result")
            job = Job("1", 1, (str(root/"foto.png"), str(root/"kaputt.png")), "jpeg", "hoch", state="partial")
            good = jpegdrop.empty_result(Path(job.paths[0]), "jpeg", "hoch")
            good.update(target=str(output), src_bytes=10, out_bytes=6, status="warning",
                        warnings=[{"code":"transparency_flattened", "message":"Transparenz entfernt"}])
            bad = jpegdrop.empty_result(Path(job.paths[1]), "jpeg", "hoch")
            bad.update(error="Ungültiges Profil", error_code="invalid_color_profile")
            job.results = [good, bad]; job.completed = 2
            pending = Job("2", 2, (str(root/"wartend.png"),), "png", "verlustfrei")
            controller = JobsWindowController.alloc().init()
            controller._build()
            controller.refresh([job, pending])
            self.assertTrue(controller._cancel.isEnabled())
            self.assertFalse(controller._reveal.isEnabled())
            controller._job_table.selectRowIndexes_byExtendingSelection_(F.NSIndexSet.indexSetWithIndex_(0), False)
            controller.tableViewSelectionDidChange_(F.NSNotification.notificationWithName_object_("selection", controller._job_table))
            self.assertEqual(controller.numberOfRowsInTableView_(controller._result_table), 2)
            self.assertFalse(controller._cancel.isEnabled())
            self.assertTrue(controller._reveal.isEnabled())
            self.assertIn("Transparenz entfernt", controller._detail.string())
            self.assertIn(str(output), controller._detail.string())
            controller._result_table.selectRowIndexes_byExtendingSelection_(F.NSIndexSet.indexSetWithIndex_(1), False)
            controller.tableViewSelectionDidChange_(F.NSNotification.notificationWithName_object_("selection", controller._result_table))
            self.assertIn("Ungültiges Profil", controller._detail.string())
            controller.refresh([job, pending])
            self.assertEqual(controller.selected_job().id, "1")
            self.assertIn("Ungültiges Profil", controller._detail.string())
            controller._window.close()
            self.assertIsNotNone(controller._window.contentView())


if __name__ == "__main__":
    unittest.main()
