"""Auftragslebenszyklus mit echten CLI-Prozessen und kontrolliert langsamen Encodern."""
from pathlib import Path
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jpegdrop
from app.jobs import JobManager
from PIL import Image

SCRIPT = str(Path(jpegdrop.__file__).resolve())
SETTINGS = {"codec": "jpeg", "preset": "hoch"}


def wait_for(predicate, seconds=6):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("Erwarteter Zustand blieb aus")


class JobsTests(unittest.TestCase):
    def test_queued_image_options_are_independent_of_changed_settings(self):
        wrapper = self.wrapper('''
import time
while not (__import__('pathlib').Path(sys.argv[-1]).parent/'release').exists(): time.sleep(.01)
raise SystemExit(jpegdrop.main())
''')
        first = self.image("first.png", "RGBA")
        second = self.image("second.png", "RGBA")
        for source in (first, second):
            Image.new("RGBA", (48, 48), (200, 100, 50, 64)).save(source)
        settings = {"codec": "webp", "preset": "verlustfrei", "max_edge": 24,
                    "preserve_alpha": True, "background": "#123456"}
        manager = self.manager(wrapper)
        manager.submit([str(first)], settings)
        wait_for(lambda: manager.snapshot()[1][0].pid is not None)
        manager.submit([str(second)], settings)
        settings.update(max_edge=1, preserve_alpha=False, background="#FFFFFF")
        self.assertEqual(manager.snapshot()[1][1].state, "queued")
        (self.root/'release').touch()
        wait_for(lambda: not manager.has_active_jobs)
        for job in manager.snapshot()[1]:
            self.assertEqual(job.state, "completed", job.problem)
            self.assertEqual(job.options.max_edge, 24)
            self.assertTrue(job.options.preserve_alpha)
            self.assertEqual(job.options.background, "#123456")
            with Image.open(job.targets[0]) as output:
                self.assertEqual(output.size, (24, 24))
                self.assertEqual(output.mode, "RGBA")

    def test_external_python_paths_cannot_replace_conversion_dependencies(self):
        from unittest.mock import patch
        poison = self.root / "external" / "PIL"
        poison.mkdir(parents=True)
        (poison / "__init__.py").write_text('raise RuntimeError("External Python path loaded")\n')
        source = self.image()
        manager = self.manager()
        with patch.dict(os.environ, {"PYTHONPATH": str(poison.parent)}):
            manager.submit([str(source)], SETTINGS)
            wait_for(lambda: not manager.has_active_jobs)
        job = manager.snapshot()[1][0]
        self.assertEqual(job.state, "completed", job.problem)
        self.assertEqual(len(job.targets), 1)
        with Image.open(job.targets[0]) as converted:
            self.assertEqual(converted.size, (48, 48))
            self.assertFalse(converted.getexif())

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="jpegdrop_jobs_")
        self.root = Path(self.directory.name)
        self.managers = []

    def tearDown(self):
        for manager in self.managers:
            manager.shutdown()
        self.directory.cleanup()

    def image(self, name="foto.png", mode="RGB"):
        path = self.root / name
        Image.new(mode, (48, 48), "red").save(path)
        return path

    def manager(self, script=SCRIPT, **kwargs):
        manager = JobManager(sys.executable, str(script), **kwargs)
        self.managers.append(manager)
        return manager

    def wrapper(self, code):
        path = self.root / "slow.py"
        path.write_text(f"import sys\nsys.path.insert(0, {str(Path(SCRIPT).parent)!r})\nimport jpegdrop\n" + code)
        return path

    def blocked_wrapper(self, *, stubborn=False):
        return self.wrapper(f'''
import os, signal, time
original = jpegdrop.process_one
counter = 0
def slow(path, *args, **kwargs):
    global counter
    counter += 1
    if counter == 2:
        temp = path.parent / (jpegdrop.temporary_prefix() + 'controlled.jpg')
        temp.write_bytes(b'unfinished')
        (path.parent/'encoder-pid').write_text(str(os.getpid()))
        {'signal.signal(signal.SIGTERM, signal.SIG_IGN)' if stubborn else ''}
        while not (path.parent/'release').exists(): time.sleep(0.02)
    return original(path, *args, **kwargs)
jpegdrop.process_one = slow
raise SystemExit(jpegdrop.main())
''')

    def test_events_match_json_and_partial_exit(self):
        good = self.image()
        bad = self.root / "kaputt.png"
        bad.write_bytes(b"broken")
        args = [sys.executable, "-B", SCRIPT, "--codec", "jpeg", "--preset", "hoch"]
        event_run = subprocess.run([*args, "--events", str(good), str(bad)], capture_output=True, check=False)
        events = [json.loads(line) for line in event_run.stdout.splitlines()]
        self.assertEqual([e["event"] for e in events],
                         ["started", "image_started", "publishing", "result", "image_started", "result", "finished"])
        self.assertEqual(event_run.returncode, 1)
        results = [e["result"] for e in events if e["event"] == "result"]
        normal = subprocess.run([*args, "--json", str(good), str(bad)], capture_output=True, check=False)
        normal_results = json.loads(normal.stdout)
        for a, b in zip(results, normal_results):
            a.pop("target"); b.pop("target")
            self.assertEqual(a, b)
        self.assertEqual(events[-1], {"event": "finished", "total": 2, "completed": 2})
        invalid = subprocess.run([*args, "--json", "--events", str(good)], capture_output=True)
        self.assertEqual(invalid.returncode, 2)

    def test_queue_is_serial_and_settings_are_snapshots(self):
        first, second = self.image("first.png"), self.image("second.png")
        manager = self.manager(self.blocked_wrapper(), max_active=2)
        settings = dict(SETTINGS)
        first_id = manager.submit([str(first), str(second)], settings)
        wait_for(lambda: (self.root / "encoder-pid").exists())
        second_id = manager.submit([str(first)], settings)
        settings.update(codec="png", preset="verlustfrei")
        with self.assertRaisesRegex(ValueError, "voll"):
            manager.submit([str(first)], settings)
        jobs = manager.snapshot()[1]
        self.assertEqual([j.state for j in jobs], ["running", "queued"])
        self.assertIsNone(jobs[1].pid)
        self.assertEqual(jobs[0].completed, 1)
        self.assertEqual(jobs[0].current, str(second))
        self.assertTrue(manager.has_active_jobs)
        (self.root / "release").touch()
        self.assertTrue(manager.wait_idle(10))
        jobs = manager.snapshot()[1]
        self.assertEqual([j.id for j in jobs], [first_id, second_id])
        self.assertEqual([j.codec for j in jobs], ["jpeg", "jpeg"])
        self.assertTrue(all(j.state == "completed" for j in jobs))
        self.assertEqual(len({p for j in jobs for p in j.targets}), 3)
        jobs[0].results[0]["target"] = "outside mutation"
        self.assertNotEqual(manager.snapshot()[1][0].targets[0], "outside mutation")
        self.assertFalse(manager.has_active_jobs)

    def test_update_drains_existing_queue_and_blocks_new_drops_until_resumed(self):
        first, second = self.image("first.png"), self.image("second.png")
        manager = self.manager(self.blocked_wrapper())
        manager.submit([str(first), str(second)], SETTINGS)
        wait_for(lambda: (self.root / "encoder-pid").exists())
        manager.submit([str(first)], SETTINGS)
        self.assertFalse(manager.prepare_for_update())
        with self.assertRaisesRegex(ValueError, "Update"):
            manager.submit([str(second)], SETTINGS)
        (self.root / "release").touch()
        self.assertTrue(manager.wait_idle(10))
        jobs = manager.snapshot()[1]
        self.assertEqual([job.state for job in jobs], ["completed", "completed"])
        self.assertEqual(sum(len(job.targets) for job in jobs), 3)
        self.assertTrue(manager.prepare_for_update())
        with self.assertRaisesRegex(ValueError, "Update"):
            manager.submit([str(second)], SETTINGS)
        manager.resume_after_update()
        manager.submit([str(second)], SETTINGS)
        self.assertTrue(manager.wait_idle(10))
        self.assertEqual(manager.snapshot()[1][-1].state, "completed")
        self.assertFalse(manager.has_active_jobs)

    def test_cancel_queued_and_running_preserves_completed_outputs(self):
        first, second = self.image("first.png"), self.image("second.png")
        before = {p: p.read_bytes() for p in (first, second)}
        manager = self.manager(self.blocked_wrapper(), cancel_grace=0.15)
        active = manager.submit([str(first), str(second)], SETTINGS)
        wait_for(lambda: (self.root / "encoder-pid").exists())
        queued = manager.submit([str(first)], SETTINGS)
        manager.cancel(queued)
        manager.cancel(active)
        self.assertTrue(manager.wait_idle(6))
        jobs = manager.snapshot()[1]
        self.assertEqual([j.state for j in jobs], ["cancelled", "cancelled"])
        self.assertEqual(jobs[0].completed, 1)
        self.assertEqual(len(jobs[0].targets), 1)
        self.assertTrue(Path(jobs[0].targets[0]).is_file())
        self.assertEqual(jobs[0].results[1]["status"], "cancelled")
        self.assertEqual(jobs[1].completed, 0)
        self.assertEqual(jobs[1].results[0]["status"], "cancelled")
        self.assertEqual(list(self.root.glob(".jpegdrop-*")), [])
        for p, data in before.items(): self.assertEqual(p.read_bytes(), data)
        pid = int((self.root / "encoder-pid").read_text())
        with self.assertRaises(ProcessLookupError): os.kill(pid, 0)
        self.assertIn("abgebrochen", jpegdrop.summary_line(jobs[0].results))

    def test_forced_cancel_removes_only_own_temporary_files(self):
        first, second = self.image("first.png"), self.image("second.png")
        foreign = self.root / ".jpegdrop-other-job.jpg"
        foreign.write_bytes(b"keep me")
        manager = self.manager(self.blocked_wrapper(stubborn=True), cancel_grace=0.1)
        identifier = manager.submit([str(first), str(second)], SETTINGS)
        wait_for(lambda: (self.root / "encoder-pid").exists())
        manager.cancel(identifier)
        self.assertTrue(manager.wait_idle(6))
        self.assertEqual(list(self.root.glob(f".jpegdrop-{identifier}-*")), [])
        self.assertEqual(foreign.read_bytes(), b"keep me")
        self.assertTrue((self.root / "first.jpg").is_file())
        self.assertFalse((self.root / "second.jpg").exists())

    def test_cancel_during_publication_finishes_the_reserved_output(self):
        first, second = self.image("first.png"), self.image("second.png")
        wrapper = self.wrapper('''
import errno, time
original_copy = jpegdrop.shutil.copyfileobj
def no_links(*args): raise OSError(errno.EOPNOTSUPP, 'controlled fallback')
def slow_copy(source, target):
    marker = __import__('pathlib').Path(sys.argv[-1]).parent
    (marker/'publishing-ready').touch()
    while not (marker/'release').exists(): time.sleep(0.01)
    return original_copy(source, target)
jpegdrop.os.link = no_links
jpegdrop.shutil.copyfileobj = slow_copy
raise SystemExit(jpegdrop.main())
''')
        manager = self.manager(wrapper, cancel_grace=0.05)
        identifier = manager.submit([str(first), str(second)], SETTINGS)
        wait_for(lambda: (self.root/'publishing-ready').exists())
        manager.cancel(identifier)
        time.sleep(0.15)
        self.assertTrue(manager.has_active_jobs)
        (self.root/'release').touch()
        self.assertTrue(manager.wait_idle(6))
        job = manager.snapshot()[1][0]
        self.assertEqual(job.completed, 1)
        self.assertEqual(job.state, "cancelled")
        self.assertEqual(job.results[0]["status"], "ok")
        with Image.open(job.targets[0]) as image: image.load()
        self.assertFalse((self.root/'second.jpg').exists())
        self.assertEqual(list(self.root.glob('.jpegdrop-*')), [])

    def test_cancel_stops_the_process_group_including_children(self):
        source = self.image()
        wrapper = self.wrapper('''
import subprocess, time, os
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
root = Path(sys.argv[-1]).parent
(root/'child-pid').write_text(str(child.pid))
print('{"event":"started","total":1}', flush=True)
time.sleep(60)
''')
        manager = self.manager(wrapper, cancel_grace=0.05)
        identifier = manager.submit([str(source)], SETTINGS)
        wait_for(lambda: (self.root/'child-pid').exists())
        pid = int((self.root/'child-pid').read_text())
        manager.cancel(identifier)
        self.assertTrue(manager.wait_idle(6))
        state = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
        self.assertTrue(not state or state.startswith('Z'), state)

    def test_shutdown_waits_and_rejects_new_jobs(self):
        first, second = self.image("first.png"), self.image("second.png")
        manager = self.manager(self.blocked_wrapper(stubborn=True), cancel_grace=0.1)
        manager.submit([str(first), str(second)], SETTINGS)
        wait_for(lambda: (self.root / "encoder-pid").exists())
        manager.submit([str(first)], SETTINGS)
        manager.shutdown()
        self.assertTrue(manager.stopped)
        self.assertFalse(manager.has_active_jobs)
        with self.assertRaisesRegex(ValueError, "beendet"):
            manager.submit([str(first)], SETTINGS)
        self.assertTrue(all(j.state == "cancelled" for j in manager.snapshot()[1]))

    def test_partial_results_keep_warnings_errors_and_skipped_files(self):
        alpha = self.image("alpha.png", "RGBA")
        Image.new("RGBA", (48,48), (255, 0, 0, 50)).save(alpha)
        bad = self.root / "bad.png"; bad.write_bytes(b"broken")
        manager = self.manager()
        manager.submit([str(alpha), str(bad)], SETTINGS,
                       skipped={str(self.root / "a.pdf"): "Format nicht unterstützt"})
        self.assertTrue(manager.wait_idle(8))
        job = manager.snapshot()[1][0]
        self.assertEqual(job.state, "partial")
        self.assertEqual(job.completed, 2)
        self.assertEqual([r["status"] for r in job.all_results], ["warning", "error", "skipped"])
        self.assertEqual(len(job.targets), 1)
        self.assertIn("1 übersprungen", jpegdrop.batch_message(job.all_results))

    def test_worker_failure_and_invalid_protocol_leave_no_orphan(self):
        source = self.image()
        for code in ("raise RuntimeError('test failure')", "print('invalid JSON', flush=True)\nimport time\ntime.sleep(20)"):
            manager = self.manager(self.wrapper(code), cancel_grace=0.1)
            manager.submit([str(source)], SETTINGS)
            self.assertTrue(manager.wait_idle(8))
            job = manager.snapshot()[1][0]
            self.assertEqual(job.state, "failed")
            self.assertEqual(job.results[0]["error_code"], "worker_failed")
            self.assertTrue(job.problem)
            self.assertIsNone(job.pid)

    def test_terminal_history_is_bounded_and_finished_cancel_is_noop(self):
        source = self.image()
        manager = self.manager(history_limit=2)
        for _ in range(4):
            identifier = manager.submit([str(source)], SETTINGS)
            self.assertTrue(manager.wait_idle(6))
        self.assertEqual(len(manager.snapshot()[1]), 2)
        manager.cancel(identifier)
        self.assertEqual(manager.snapshot()[1][-1].state, "completed")


if __name__ == "__main__":
    unittest.main()
