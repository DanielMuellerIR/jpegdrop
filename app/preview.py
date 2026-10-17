"""Begrenzte Vorschauverwaltung ohne AppKit; nur das jüngste Ergebnis gilt."""
from __future__ import annotations

import copy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time

from app.profiles import OPTION_KEYS, canonical_options
from app.preview_worker import source_signature


@dataclass(frozen=True)
class PreviewRequest:
    generation: int
    source: str
    options: tuple[tuple[str, object], ...]
    profile_name: str | None

    def settings(self) -> dict:
        return dict(self.options)


class PreviewManager:
    """Ein Prozess, ein wartender Snapshot und höchstens ein fertiges Ergebnis."""

    def __init__(self, python: str, *, debounce: float = .3, timeout: float = 120):
        self.python, self.debounce, self.timeout = python, debounce, timeout
        self._condition = threading.Condition()
        self._generation = 0
        self._pending = None
        self._running = False
        self._closing = False
        self._paused = False
        self._state = {"generation": 0, "status": "empty"}
        self._artifact = None
        self._worker = threading.Thread(target=self._work, name="jpegdrop-preview", daemon=False)
        self._worker.start()

    @property
    def has_active_jobs(self):
        with self._condition:
            return self._running or self._pending is not None

    @property
    def stopped(self):
        return not self._worker.is_alive()

    def snapshot(self):
        with self._condition:
            return copy.deepcopy(self._state)

    def request(self, source: str, settings: dict, profile_name: str | None = None) -> int:
        options = canonical_options({key: value for key, value in settings.items() if key in OPTION_KEYS})
        with self._condition:
            if self._closing or self._paused:
                raise ValueError("JPEGdrop bereitet ein Update vor oder wird beendet. Keine neue Vorschau möglich.")
            self._generation += 1
            request = PreviewRequest(self._generation, os.path.abspath(source), options, profile_name)
            self._pending = (time.monotonic() + self.debounce, request)
            self._state = {"generation": request.generation, "status": "running", "request": request}
            self._condition.notify_all()
            return request.generation

    def cancel(self):
        with self._condition:
            self._generation += 1
            self._pending = None
            self._state = {"generation": self._generation, "status": "cancelled"}
            self._condition.notify_all()

    def prepare_for_update(self):
        with self._condition:
            self._paused = True
            self.cancel()
            return not self._running

    def resume_after_update(self):
        with self._condition:
            self._paused = False

    def convert(self, generation: int, jobs) -> str:
        with self._condition:
            state = self._state
            if self._closing or self._paused or state["status"] != "ready" or state["generation"] != generation:
                raise ValueError("Bitte eine aktuelle, vollständig berechnete Vorschau abwarten.")
            request = state["request"]
            if source_signature(Path(request.source)) != tuple(state["signature"]):
                self.cancel()
                raise ValueError("Die Quelle wurde verändert. Bitte die Vorschau erneut berechnen.")
            return jobs.submit([request.source], request.settings(), profile_name=request.profile_name)

    def shutdown(self, *, wait=True):
        with self._condition:
            self._closing = True
            self.cancel()
        if wait:
            self._worker.join()

    def _obsolete(self, request):
        with self._condition:
            return self._closing or request.generation != self._generation

    @staticmethod
    def _kill(proc):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()

    def _render(self, request, directory):
        root = Path(directory)
        (root / "request.json").write_text(json.dumps({"source": request.source, "settings": request.settings()}))
        script = str(Path(__file__).with_name("preview_worker.py"))
        proc = subprocess.Popen([self.python, "-I", "-B", script, directory],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        deadline = time.monotonic() + self.timeout
        try:
            while proc.poll() is None:
                if self._obsolete(request):
                    return None
                if time.monotonic() >= deadline:
                    raise ValueError("Zeitlimit der Vorschau überschritten. Bitte kleinere Bildmaße wählen.")
                with self._condition:
                    self._condition.wait(timeout=.05)
            if self._obsolete(request):
                return None
            if proc.returncode:
                raise ValueError(f"Vorschauprozess beendet (Exit {proc.returncode}).")
            return json.loads((root / "response.json").read_text())
        finally:
            self._kill(proc)

    def _cleanup_artifact(self, artifact, generation):
        try:
            artifact.cleanup()
        except OSError as error:
            # Für einen erneuten Versuch behalten; fehlendes Cleanup ist kein Erfolg.
            self._artifact = artifact
            if generation == self._generation:
                self._state = {"generation": generation, "status": "error",
                               "error": f"Temporäre Vorschau konnte nicht entfernt werden: {error}"}
            return False
        return True

    def _work(self):
        while True:
            with self._condition:
                # Artefakte gehören dem Worker; niemals fremde Temp-Verzeichnisse suchen.
                if self._artifact is not None and self._state["status"] != "ready":
                    if not self._cleanup_artifact(self._artifact, self._generation):
                        self._pending = None
                        if self._closing:
                            return
                        self._condition.wait()
                        continue
                    self._artifact = None
                if self._closing:
                    return
                if self._pending is None:
                    self._condition.wait()
                    continue
                due, request = self._pending
                delay = due - time.monotonic()
                if delay > 0:
                    self._condition.wait(timeout=delay)
                    continue
                self._pending = None
                self._running = True
            artifact = None
            try:
                artifact = tempfile.TemporaryDirectory(prefix="jpegdrop-preview-")
                response = self._render(request, artifact.name)
                with self._condition:
                    if response is not None and not self._obsolete(request):
                        error = response.get("error") or response.get("result", {}).get("error")
                        self._state = {**response, "generation": request.generation,
                                       "status": "error" if error else "ready", "error": error, "request": request}
                        if not error:
                            self._artifact, artifact = artifact, None
            except (OSError, ValueError) as error:
                with self._condition:
                    if not self._obsolete(request):
                        self._state = {"generation": request.generation, "status": "error", "error": str(error)}
            finally:
                with self._condition:
                    try:
                        if artifact is not None:
                            self._cleanup_artifact(artifact, request.generation)
                    finally:
                        self._running = False
                        self._condition.notify_all()
