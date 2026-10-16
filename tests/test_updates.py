"""Update-Übergabe: aktive Arbeit, native Fehler und Wiederaufnahme neuer Drops."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import Foundation as F
from app.updates import JDUpdateController
from app.jobs import JobManager
from tools.sparkle import validate_test_feed


class UpdateTests(unittest.TestCase):
    def test_deferred_relaunch_waits_for_jobs_and_modal_dialog(self):
        class Jobs:
            has_active_jobs = True
            paused = False

            def prepare_for_update(self):
                self.paused = True
                return not self.has_active_jobs

            def resume_after_update(self):
                self.paused = False

        jobs = Jobs()
        controller = JDUpdateController.alloc().init()
        controller.jobs = jobs
        invoked = []
        controller.can_relaunch = lambda: False
        self.assertTrue(controller.updater_shouldPostponeRelaunchForUpdate_untilInvokingBlock_(
            None, None, lambda: invoked.append("relaunch")))
        self.assertTrue(jobs.paused)
        controller.poll()
        self.assertEqual(invoked, [])
        jobs.has_active_jobs = False
        controller.poll()
        self.assertEqual(invoked, [])
        controller.can_relaunch = lambda: True
        controller.poll()
        controller.poll()
        self.assertEqual(invoked, ["relaunch"])
        self.assertTrue(controller.installing)

    def test_native_failure_reopens_drop_submission(self):
        manager = JobManager(sys.executable, "unused")
        try:
            controller = JDUpdateController.alloc().init()
            controller.jobs = manager
            controller.installing = True
            manager.prepare_for_update()
            error = F.NSError.errorWithDomain_code_userInfo_("Sparkle", 1, None)
            controller.updater_didFinishUpdateCycleForUpdateCheck_error_(None, 0, error)
            self.assertFalse(controller.installing)
            self.assertEqual(controller.last_error, error)
            # Die CLI darf hier fehlschlagen; geprüft wird die Wiederannahme
            # eines Auftrags nach einer gescheiterten Update-Installation.
            manager.submit(["unused.png"], {"codec": "jpeg", "preset": "hoch"})
        finally:
            manager.shutdown()

    def test_source_checkout_never_starts_network_updater(self):
        controller = JDUpdateController.alloc().init()
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(controller.configure(None, Path(directory)))
        self.assertFalse(controller.available)
        self.assertFalse(controller.validateMenuItem_(None))

    def test_test_feed_is_loopback_only(self):
        self.assertEqual(validate_test_feed("http://127.0.0.1:8942/appcast.xml"),
                         "http://127.0.0.1:8942/appcast.xml")
        for url in ("https://example.com/appcast.xml", "http://example.com/appcast.xml",
                    "http://localhost/feed.xml#x", "http://localhost/feed.zip",
                    "http://localhost:invalid/feed.xml"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_test_feed(url)


if __name__ == "__main__":
    unittest.main()
