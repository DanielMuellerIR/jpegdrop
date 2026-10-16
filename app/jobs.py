"""Serielle Aufträge und CLI-Lebenszyklus, ohne AppKit-Abhängigkeit."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
import os
from pathlib import Path
import selectors
import signal
import subprocess
import threading
import time
import uuid
import json
import tempfile

import jpegdrop
from app.input_plan import InventoryOptions, InventoryCancelled, inventory_inputs

ACTIVE_STATES = frozenset({"queued", "scanning", "running", "cancelling"})
STATE_LABELS = {
    "queued": "Wartend", "scanning": "Wird eingelesen …", "running": "Läuft", "cancelling": "Wird abgebrochen …",
    "completed": "Abgeschlossen", "partial": "Teilweise fehlgeschlagen",
    "failed": "Fehlgeschlagen", "cancelled": "Abgebrochen",
}


@dataclass
class Job:
    id: str
    number: int
    paths: tuple[str, ...]
    codec: str
    preset: str
    options: jpegdrop.ImageOptions = field(default_factory=jpegdrop.ImageOptions)
    inventory_options: InventoryOptions = field(default_factory=InventoryOptions)
    input_paths: tuple[str, ...] = ()
    output_directories: tuple[str | None, ...] = ()
    state: str = "queued"
    results: list[dict] = field(default_factory=list)
    current: str | None = None
    completed: int = 0
    cancel_requested: bool = False
    problem: str | None = None
    pid: int | None = None
    publishing: bool = False
    optimization: str | None = None
    skipped: list[dict] = field(default_factory=list)
    profile_name: str | None = None

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_STATES

    @property
    def title(self) -> str:
        original = self.input_paths or self.paths
        name = Path(original[0]).name
        extra = f" + {len(original) - 1}" if len(original) > 1 else ""
        return f"{self.number}: {name}{extra}"

    @property
    def all_results(self) -> list[dict]:
        return self.results + self.skipped

    @property
    def targets(self) -> list[str]:
        return [r["target"] for r in self.results if r.get("target") and not r.get("error")]


class JobManager:
    """Ein Worker und höchstens ein CLI-Prozess; Snapshots gehören dem Aufrufer."""

    def __init__(self, python: str, script: str, *, max_active: int = 20,
                 history_limit: int = 50, history_result_limit: int = 10_000, cancel_grace: float = 2.0):
        self.python, self.script = python, script
        self.max_active, self.history_limit = max_active, history_limit
        self.history_result_limit = history_result_limit
        self.cancel_grace = cancel_grace
        self._condition = threading.Condition()
        self._jobs: list[Job] = []
        self._revision = 0
        self._number = 0
        self._closing = False
        self._update_paused = False
        self._worker = threading.Thread(target=self._work, name="jpegdrop-jobs", daemon=False)
        self._worker.start()

    def _changed(self) -> None:
        self._revision += 1
        self._condition.notify_all()

    @property
    def has_active_jobs(self) -> bool:
        """Update-Sperre: schließt wartende und gerade abgebrochene Aufträge ein."""
        with self._condition:
            return any(job.active for job in self._jobs)

    @property
    def stopped(self) -> bool:
        return not self._worker.is_alive()

    def snapshot(self, *, after_revision: int | None = None) -> tuple[int, list[Job] | None]:
        with self._condition:
            return self._revision, (None if after_revision == self._revision else copy.deepcopy(self._jobs))

    def submit(self, paths: list[str], settings: dict, *, skipped: dict[str, str] | None = None,
               profile_name: str | None = None) -> str:
        if not paths:
            raise ValueError("Der Auftrag enthält keine Dateien.")
        codec = jpegdrop.get_codec(settings["codec"])
        preset = jpegdrop.get_preset(codec, settings["preset"])
        options = jpegdrop.ImageOptions.from_settings(settings)
        inventory_options = InventoryOptions.from_settings(settings)
        if codec.name == "jpeg" and options.preserve_alpha:
            raise ValueError("JPEG kann keine Transparenz erhalten.")
        with self._condition:
            if self._closing:
                raise ValueError("JPEGdrop wird beendet und nimmt keine Aufträge mehr an.")
            if self._update_paused:
                raise ValueError("JPEGdrop bereitet ein Update vor. Bitte den Drop nach dem Neustart wiederholen.")
            if sum(job.active for job in self._jobs) >= self.max_active:
                raise ValueError(f"Die Warteschlange ist voll ({self.max_active} aktive Aufträge).")
            self._number += 1
            job = Job(uuid.uuid4().hex, self._number, tuple(os.path.abspath(p) for p in paths),
                      codec.name, preset.name, options=options, inventory_options=inventory_options,
                      profile_name=profile_name)
            job.input_paths = job.paths
            for path, reason in (skipped or {}).items():
                result = jpegdrop.empty_result(Path(os.path.abspath(path)), codec.name, preset.name, target_bytes=options.target_bytes)
                result.update(status="skipped", error_code="unsupported_input", skip_reason=reason)
                job.skipped.append(result)
            self._jobs.append(job)
            self._prune()
            self._changed()
            return job.id

    def prepare_for_update(self) -> bool:
        """Neue Drops sperren und atomar prüfen, ob alle bisherigen Aufträge fertig sind."""
        with self._condition:
            self._update_paused = True
            self._changed()
            return not any(job.active for job in self._jobs)

    def resume_after_update(self) -> None:
        with self._condition:
            self._update_paused = False
            self._changed()

    def cancel(self, job_id: str) -> None:
        with self._condition:
            job = next((j for j in self._jobs if j.id == job_id), None)
            if job is None or not job.active or job.cancel_requested:
                return
            job.cancel_requested = True
            if job.state == "queued":
                self._finish_missing(job, cancelled=True)
                job.state = "cancelled"
            else:
                job.state = "cancelling"
            self._changed()

    def shutdown(self, *, wait: bool = True) -> None:
        with self._condition:
            self._closing = True
            for job in self._jobs:
                if job.active:
                    self.cancel(job.id)
            self._changed()
        if wait:
            self._worker.join()

    def wait_idle(self, timeout: float) -> bool:
        with self._condition:
            return self._condition.wait_for(
                lambda: not any(j.active for j in self._jobs), timeout=timeout)

    def _prune(self) -> None:
        terminal = [j for j in self._jobs if not j.active]
        remove = {j.id for j in terminal[:-self.history_limit]} if self.history_limit else {j.id for j in terminal}
        remaining = [job for job in terminal if job.id not in remove]
        count = sum(len(job.results) + len(job.skipped) for job in remaining)
        while len(remaining) > 1 and count > self.history_result_limit:
            old = remaining.pop(0)
            count -= len(old.results) + len(old.skipped)
            remove.add(old.id)
        self._jobs[:] = [j for j in self._jobs if j.id not in remove]

    def _finish_missing(self, job: Job, *, cancelled: bool) -> None:
        for path in job.paths[len(job.results):]:
            result = jpegdrop.empty_result(Path(path), job.codec, job.preset, target_bytes=job.options.target_bytes)
            result["status"] = "cancelled" if cancelled else "error"
            result["error_code"] = "cancelled" if cancelled else "worker_failed"
            if not cancelled:
                result["error"] = job.problem or "Konvertierungsprozess ohne vollständiges Ergebnis beendet."
            job.results.append(result)

    def _work(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closing or any(j.state == "queued" for j in self._jobs))
                job = next((j for j in self._jobs if j.state == "queued"), None)
                if job is None:
                    if self._closing:
                        return
                    continue
                job.state = "scanning"
                self._changed()
            try:
                inventory = inventory_inputs(job.input_paths, job.inventory_options,
                                             cancelled=lambda: job.cancel_requested)
                with self._condition:
                    job.paths = tuple(entry.source for entry in inventory.entries)
                    job.output_directories = tuple(entry.output_directory for entry in inventory.entries)
                    for issue in inventory.issues:
                        result = jpegdrop.empty_result(Path(issue.source), job.codec, job.preset, target_bytes=job.options.target_bytes)
                        result.update(status="error" if issue.error else "skipped",
                                      error=issue.message if issue.error else None,
                                      error_code="inventory_error" if issue.error else "unsupported_input",
                                      skip_reason=issue.message)
                        job.skipped.append(result)
                    if not job.cancel_requested:
                        job.state = "running"
                    self._changed()
                if job.paths and not job.cancel_requested:
                    self._run(job)
            except InventoryCancelled:
                pass
            except Exception as exc:
                with self._condition:
                    job.problem = f"{type(exc).__name__}: {exc}"
            finally:
                self._cleanup(job)
                with self._condition:
                    missing = len(job.results) < len(job.paths) or job.state == "cancelling"
                    self._finish_missing(job, cancelled=job.cancel_requested)
                    job.current, job.pid, job.optimization = None, None, None
                    successes = sum(bool(r.get("target")) and not r.get("error") for r in job.results)
                    failures = sum(bool(r.get("error")) for r in job.all_results)
                    if job.cancel_requested and missing:
                        job.state = "cancelled"
                    elif failures or job.problem:
                        job.state = "partial" if successes else "failed"
                    else:
                        job.state = "completed"
                    self._prune()
                    self._changed()

    @staticmethod
    def _signal_group(proc: subprocess.Popen, sig: int) -> None:
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass

    def _event(self, job: Job, line: bytes) -> None:
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError("Ungültiges CLI-Ereignis")
        kind = event.get("event")
        with self._condition:
            if kind == "publishing":
                if event.get("index") != len(job.results):
                    raise ValueError("Unstimmige CLI-Veröffentlichung")
                job.publishing = True
            elif kind == "image_started":
                index = event.get("index")
                if index != len(job.results) or index >= len(job.paths):
                    raise ValueError("Unstimmiger CLI-Fortschritt")
                job.current = job.paths[index]
                job.optimization = None
                self._changed()
            elif kind == "optimizing":
                attempt, maximum = event.get("attempt"), event.get("maximum")
                if (event.get("index") != len(job.results) or type(attempt) is not int
                        or type(maximum) is not int or not 1 <= attempt <= maximum <= 324):
                    raise ValueError("Unstimmiger Optimierungsfortschritt")
                job.optimization = f"Größensuche: {event.get('attempt')} / {event.get('maximum')}"
                self._changed()
            elif kind == "result":
                result = event.get("result")
                index = event.get("index")
                if (index != len(job.results) or index >= len(job.paths)
                        or not isinstance(result, dict) or result.get("source") != job.paths[index]
                        or not set(jpegdrop.empty_result(Path(""), job.codec, job.preset)) <= result.keys()):
                    raise ValueError("Unvollständiges CLI-Ergebnis")
                job.publishing = False
                job.results.append(result)
                job.completed = len(job.results)
                job.current = None
                job.optimization = None
                self._changed()
            elif kind not in {"started", "finished"}:
                raise ValueError("Unbekanntes CLI-Ereignis")

    def _run(self, job: Job) -> None:
        with tempfile.TemporaryDirectory(prefix=f"jpegdrop-input-{job.id}-") as directory:
            plan = Path(directory) / "inputs.json"
            plan.write_text(json.dumps([{"source": source, "output_directory": output}
                            for source, output in zip(job.paths, job.output_directories)], ensure_ascii=True))
            if plan.stat().st_size > 32 * 1024 * 1024:
                raise ValueError("Der Eingabeplan überschreitet 32 MiB.")
            self._run_plan(job, plan)

    def _run_plan(self, job: Job, plan: Path) -> None:
        cmd = [self.python, "-I", "-B", self.script, "--codec", job.codec,
               "--preset", job.preset, "--max-edge", str(job.options.max_edge),
               "--preserve-alpha" if job.options.preserve_alpha else "--flatten-alpha",
               "--background", job.options.background, "--target-size", f"{job.options.target_bytes}B",
               "--target-resize" if job.options.allow_target_resize else "--no-target-resize",
               "--target-min-edge", str(job.options.minimum_target_edge),
               "--events", "--input-plan", str(plan)]
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", JPEGDROP_JOB_ID=job.id)
        with self._condition:
            if job.cancel_requested:
                return
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, env=env, start_new_session=True)
        with self._condition:
            job.pid = proc.pid
            self._changed()
        buffer, stderr = b"", b""
        stop_at = None
        deadline = time.monotonic() + 300 + 60 * len(job.paths)
        selector = selectors.DefaultSelector()
        try:
            selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
            selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                now = time.monotonic()
                with self._condition:
                    stop = job.cancel_requested
                if now >= deadline and stop_at is None:
                    with self._condition:
                        job.problem = "Zeitlimit der Konvertierung überschritten."
                    stop = True
                if stop and stop_at is None:
                    self._signal_group(proc, signal.SIGTERM)
                    stop_at = now
                if stop_at is not None and now - stop_at >= self.cancel_grace and not job.publishing:
                    self._signal_group(proc, signal.SIGKILL)
                for key, _mask in selector.select(0.05):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if key.data == "stderr":
                        stderr = (stderr + chunk)[-8192:]
                    else:
                        buffer += chunk
                        while b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            self._event(job, line)
                        if len(buffer) > 1024 * 1024:
                            raise ValueError("CLI-Ereignis überschreitet die Größenbegrenzung")
                # Ein Kind darf mit geerbten Pipes den Abschluss nicht verhindern.
                if proc.poll() is not None:
                    self._signal_group(proc, signal.SIGKILL)
            if buffer.strip():
                self._event(job, buffer)
            code = proc.wait()
            with self._condition:
                if not job.cancel_requested and (len(job.results) != len(job.paths)
                                                or code != min(sum(bool(r.get("error")) for r in job.results), 125)):
                    job.problem = (stderr.decode("utf-8", "replace").strip()
                                   or f"Konvertierungsprozess beendet (Exit {code}).")
        finally:
            # Auch ein Protokollfehler darf keinen Encoder weiterlaufen lassen.
            if proc.poll() is None:
                self._signal_group(proc, signal.SIGTERM)
                try:
                    proc.wait(timeout=self.cancel_grace)
                except subprocess.TimeoutExpired:
                    self._signal_group(proc, signal.SIGKILL)
            proc.wait()
            self._signal_group(proc, signal.SIGKILL)
            selector.close()
            proc.stdout.close()
            proc.stderr.close()

    @staticmethod
    def _cleanup(job: Job) -> None:
        # Die zufällige Auftrags-ID ist nur diesem Prozess bekannt. Keinesfalls
        # generisch .jpegdrop-* löschen: andere CLI-Läufe dürfen parallel arbeiten.
        parents = {Path(output) if output is not None else Path(source).parent
                   for source, output in zip(job.paths, job.output_directories)}
        for parent in parents:
            try:
                for path in parent.glob(f"{jpegdrop.TEMP_PREFIX}{job.id}-*"):
                    path.unlink(missing_ok=True)
            except OSError as exc:
                job.problem = f"Temporäre Datei konnte nicht entfernt werden: {exc}"
