"""Echte Kodierung, Quellschutz und Lebenszyklus der optionalen Vorschau."""
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

from PIL import Image
import pytest

import jpegdrop
from app.jobs import JobManager
from app.preview import PreviewManager
from app.preview_worker import render


def wait_for(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if value := predicate():
            return value
        time.sleep(.02)
    raise AssertionError("Vorschauzustand nicht rechtzeitig erreicht")


@pytest.mark.parametrize("codec,preset,alpha,limit", [
    ("jpeg", "hoch", False, 0), ("jpeg", "hoch", False, 3000),
    ("png", "verlustfrei", True, 0), ("png", "klein_256", True, 0),
    ("webp", "verlustfrei", True, 0), ("avif", "hoch", True, 0)])
def test_preview_uses_identical_encoded_bytes_and_dimensions(tmp_path, codec, preset, alpha, limit):
    source = tmp_path / 'Foto "ä - Original.png'
    image = Image.effect_noise((240, 160), 75).convert("RGBA")
    image.putalpha(Image.linear_gradient("L").resize(image.size))
    image.save(source)
    original = source.read_bytes()
    output = tmp_path / "regular"
    settings = {**jpegdrop.DEFAULT_SETTINGS, "codec": codec, "preset": preset,
                "preserve_alpha": alpha, "background": "#124578", "max_edge": 120,
                "target_bytes": limit, "output_mode": "directory", "output_directory": str(output)}
    preview = tmp_path / "preview"; preview.mkdir()
    response = render(source, settings, preview)
    result = response["result"]
    assert result["error"] is None
    assert not output.exists()
    assert source.read_bytes() == original
    actual = jpegdrop.process_one(source, jpegdrop.get_codec(codec), preset, "-klein",
        options=jpegdrop.ImageOptions.from_settings(settings), output_directory=output)
    assert Path(result["target"]).read_bytes() == Path(actual["target"]).read_bytes()
    assert (result["out_bytes"], result["output_width"], result["output_height"]) == (
        actual["out_bytes"], actual["output_width"], actual["output_height"])
    with Image.open(response["after"]) as after:
        assert after.size == (actual["output_width"], actual["output_height"])
    with Image.open(response["before"]) as before:
        assert before.size == image.size
        assert "A" in before.getbands()


def test_orientation_and_error_are_shared_with_core(tmp_path):
    source = tmp_path / "portrait.jpg"
    exif = Image.Exif(); exif[274] = 6
    Image.new("RGB", (120, 80), "red").save(source, exif=exif)
    directory = tmp_path / "preview"; directory.mkdir()
    response = render(source, jpegdrop.DEFAULT_SETTINGS, directory)
    assert (response["result"]["source_width"], response["result"]["source_height"]) == (80, 120)
    with Image.open(response["before"]) as before:
        assert before.size == (80, 120)
    failed = tmp_path / "failed"; failed.mkdir()
    response = render(source, {**jpegdrop.DEFAULT_SETTINGS, "target_bytes": 1}, failed)
    assert response["result"]["error_code"] == "target_size_unreachable"
    assert response["result"]["target"] is None
    assert list((failed / "encoded").iterdir()) == []


def test_preview_rejects_links_like_regular_inventory(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (10, 10)).save(source)
    link = tmp_path / "link.png"; link.symlink_to(source)
    with pytest.raises(ValueError, match="Symbolische Links"):
        render(link, jpegdrop.DEFAULT_SETTINGS, tmp_path)
    assert set(tmp_path.iterdir()) == {source, link}


def test_latest_request_snapshot_conversion_and_cleanup(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (300, 200), "orange").save(source)
    original = source.read_bytes()
    manager = PreviewManager(sys.executable, debounce=.05)
    jobs = JobManager(sys.executable, str(Path(jpegdrop.__file__)))
    artifact = None
    try:
        settings = {**jpegdrop.DEFAULT_SETTINGS, "codec": "webp", "preset": "verlustfrei", "max_edge": 80,
                    "output_mode": "subfolder", "output_subdir": "Reviewed"}
        old = manager.request(str(source), settings, "Betrachtet")
        settings["max_edge"] = 40
        latest = manager.request(str(source), settings, "Neu")
        settings["max_edge"] = 10
        state = wait_for(lambda: (s if (s := manager.snapshot())["status"] == "ready" else None))
        assert state["generation"] == latest
        assert state["result"]["output_width"] == 40
        assert state["request"].settings()["max_edge"] == 40
        assert len(list(tmp_path.iterdir())) == 1
        with pytest.raises(ValueError):
            manager.convert(old, jobs)
        manager.convert(latest, jobs)
        assert jobs.wait_idle(15)
        job = jobs.snapshot()[1][0]
        assert job.options.max_edge == 40 and job.profile_name == "Neu"
        assert job.state == "completed"
        assert job.results[0]["out_bytes"] == state["result"]["out_bytes"]
        assert Path(job.targets[0]).read_bytes() == Path(state["result"]["target"]).read_bytes()
        artifact = Path(state["before"]).parent
        source.write_bytes(original + b"changed")
        with pytest.raises(ValueError, match="Quelle wurde verändert"):
            manager.convert(latest, jobs)
        wait_for(lambda: not artifact.exists())
    finally:
        manager.shutdown(); jobs.shutdown()
    assert manager.stopped
    assert artifact is not None and not artifact.exists()


def test_stale_running_result_and_intermediate_requests_are_discarded(tmp_path):
    started, release = threading.Event(), threading.Event()
    seen, directories = [], []
    def renderer(self, request, directory):
        seen.append(request.generation); directories.append(Path(directory))
        if len(seen) == 1:
            started.set(); assert release.wait(5)
        return {"result": {}, "signature": []}
    with patch.object(PreviewManager, "_render", renderer):
        manager = PreviewManager(sys.executable, debounce=0)
        try:
            manager.request("source.png", jpegdrop.DEFAULT_SETTINGS)
            assert started.wait(5)
            for edge in range(1, 21):
                latest = manager.request("source.png", {**jpegdrop.DEFAULT_SETTINGS, "max_edge": edge})
            assert manager.snapshot()["status"] == "running"
            release.set()
            state = wait_for(lambda: (s if (s := manager.snapshot())["status"] == "ready" else None))
            assert state["generation"] == latest
            assert seen == [1, latest]
        finally:
            release.set(); manager.shutdown()
        assert all(not path.exists() for path in directories)


@pytest.fixture
def blocked_preview_process(monkeypatch):
    processes, directories = [], []
    popen = subprocess.Popen
    def start(command, **kwargs):
        directories.append(Path(command[-1]))
        process = popen([sys.executable, "-I", "-B", "-c", "import time; time.sleep(60)"], **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr("app.preview.subprocess.Popen", start)
    return processes, directories


def test_timeout_reaps_process_and_artifacts(blocked_preview_process):
    processes, directories = blocked_preview_process
    manager = PreviewManager(sys.executable, debounce=0, timeout=.05)
    try:
        manager.request("source.png", jpegdrop.DEFAULT_SETTINGS)
        state = wait_for(lambda: (s if (s := manager.snapshot())["status"] == "error" else None))
        assert "Zeitlimit" in state["error"]
        wait_for(lambda: not manager.has_active_jobs)
        assert len(processes) == 1 and processes[0].poll() is not None
        assert all(not path.exists() for path in directories)
    finally:
        manager.shutdown()


def test_cancel_update_and_shutdown_reap_process_and_artifacts(blocked_preview_process):
    processes, directories = blocked_preview_process
    manager = PreviewManager(sys.executable, debounce=0)
    try:
        manager.request("source.png", jpegdrop.DEFAULT_SETTINGS)
        wait_for(lambda: len(processes) == 1)
        assert not manager.prepare_for_update()
        with pytest.raises(ValueError, match="Update"):
            manager.request("source.png", jpegdrop.DEFAULT_SETTINGS)
        wait_for(lambda: not manager.has_active_jobs)
        assert processes[0].poll() is not None
        assert all(not path.exists() for path in directories)
        manager.resume_after_update()
        manager.request("source.png", jpegdrop.DEFAULT_SETTINGS)
        wait_for(lambda: len(processes) == 2)
        manager.cancel()
        assert manager.snapshot()["status"] == "cancelled"
        wait_for(lambda: not manager.has_active_jobs)
        assert processes[1].poll() is not None
        manager.request("source.png", jpegdrop.DEFAULT_SETTINGS)
        wait_for(lambda: len(processes) == 3)
    finally:
        manager.shutdown()
    assert manager.stopped and not manager.has_active_jobs
    assert all(process.poll() is not None for process in processes)
    assert all(not path.exists() for path in directories)


@pytest.mark.parametrize("failure", ["create", "discard", "replace"])
def test_temp_errors_are_visible_and_worker_recovers(tmp_path, monkeypatch, failure):
    directories = []
    failing = threading.Event()
    temporary_directory = tempfile.TemporaryDirectory
    def create(**kwargs):
        if failure == "create" and failing.is_set():
            raise OSError("Temp-Anlage fehlgeschlagen")
        artifact = temporary_directory(dir=tmp_path, **kwargs)
        directories.append(Path(artifact.name))
        cleanup = artifact.cleanup
        def remove():
            if failure != "create" and failing.is_set():
                raise OSError("Temp-Bereinigung fehlgeschlagen")
            cleanup()
        artifact.cleanup = remove
        return artifact
    def renderer(self, request, directory):
        return {"error": "Encoderfehler"} if failure == "discard" and failing.is_set() else {"result": {}}
    monkeypatch.setattr("app.preview.tempfile.TemporaryDirectory", create)
    monkeypatch.setattr(PreviewManager, "_render", renderer)
    manager = PreviewManager(sys.executable, debounce=0)
    try:
        if failure == "replace":
            manager.request("source.png", jpegdrop.DEFAULT_SETTINGS)
            wait_for(lambda: manager.snapshot()["status"] == "ready")
        failing.set()
        failed = manager.request("source.png", jpegdrop.DEFAULT_SETTINGS)
        state = wait_for(lambda: (s if (s := manager.snapshot())["status"] == "error"
                                 and "Temp-" in s["error"] else None), timeout=2)
        assert state["generation"] == failed
        assert "Temp-" in state["error"]
        wait_for(lambda: not manager.has_active_jobs, timeout=2)
        assert not manager.stopped
        assert manager.prepare_for_update()
        failing.clear()
        manager.resume_after_update()
        latest = manager.request("source.png", jpegdrop.DEFAULT_SETTINGS)
        state = wait_for(lambda: (s if (s := manager.snapshot())["status"] == "ready" else None), timeout=2)
        assert state["generation"] == latest
        assert all(not directory.exists() for directory in directories[:-1])
    finally:
        failing.clear()
        manager.shutdown()
    assert manager.stopped and not manager.has_active_jobs
    assert all(not directory.exists() for directory in directories)
