"""Native Zustände ohne Fensterfokus; visuelle Abnahme erfolgt separat."""
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import AppKit as A
from PIL import Image
import pytest

import jpegdrop
from app.menu_app import AppDelegate, SettingsWindowController
from app.preview import PreviewManager
from app.preview_window import PreviewWindowController
from tests.test_preview import wait_for


def test_form_changes_block_old_conversion_cancel_preserves_settings(tmp_path):
    A.NSApplication.sharedApplication().setActivationPolicy_(A.NSApplicationActivationPolicyProhibited)
    source = tmp_path / "alpha.png"
    Image.new("RGBA", (300, 200), (200, 50, 50, 100)).save(source)
    with patch.object(jpegdrop, "SETTINGS_DIR", tmp_path), patch.object(jpegdrop, "SETTINGS_PATH", tmp_path / "settings.json"):
        jpegdrop.save_settings(jpegdrop.DEFAULT_SETTINGS)
        original = jpegdrop.SETTINGS_PATH.read_bytes()
        form = SettingsWindowController.alloc().init(); form._populate_from_settings()
        delegate = AppDelegate.alloc().init()
        delegate._settings_controller = form
        manager = PreviewManager(sys.executable, debounce=0)
        controller = PreviewWindowController.alloc().init()
        controller.owner = delegate; controller.manager = manager
        controller._build(); controller._source = str(source)
        window = controller._window
        controller._window = SimpleNamespace(isVisible=lambda: True)
        values = form._form_settings()
        with patch.object(PreviewWindowController, "_form", lambda self: form._form_settings()):
            controller._last_form = values
            controller._request(values)
            state = wait_for(lambda: (s if (s := manager.snapshot())["status"] == "ready" else None))
            controller.poll()
            assert controller._convert.isEnabled()
            assert "300 × 200 px" in controller._after.stringValue()
            manager.debounce = 30
            form._max_edge.setStringValue_("60")
            controller.convert_(None)
            assert delegate._jobs.snapshot()[1] == []
            assert not controller._convert.isEnabled()
            assert state["request"].settings()["max_edge"] == 0
            assert manager.snapshot()["request"].settings()["max_edge"] == 60
            form._max_edge.setStringValue_("ungültig")
            controller.poll()
            assert manager.snapshot()["status"] == "cancelled"
            assert not controller._canvas.images
            assert not controller._convert.isEnabled()
            form._max_edge.setStringValue_("60")
            controller.windowWillClose_(None)
            assert jpegdrop.SETTINGS_PATH.read_bytes() == original
            assert form._max_edge.stringValue() == "60"
        manager.shutdown(); delegate._jobs.shutdown()
        controller._window = window; window.close()
        form._window.close()
        assert not Path(state["before"]).parent.exists()


def test_update_gate_includes_preview_and_reopens_both_managers(tmp_path):
    delegate = AppDelegate.alloc().init()
    delegate._preview = PreviewManager(sys.executable, debounce=30)
    try:
        delegate._preview.request(str(tmp_path / "source.png"), jpegdrop.DEFAULT_SETTINGS)
        assert delegate.has_active_jobs
        assert delegate.prepare_for_update()
        assert delegate._preview_paused
        for manager, args in ((delegate._preview, ("source.png", jpegdrop.DEFAULT_SETTINGS)),
                              (delegate._jobs, (["source.png"], jpegdrop.DEFAULT_SETTINGS))):
            with pytest.raises(ValueError, match="Update"):
                manager.request(*args) if manager is delegate._preview else manager.submit(*args)
        delegate.resume_after_update()
        assert not delegate._preview_paused
        delegate._preview.request("source.png", jpegdrop.DEFAULT_SETTINGS)
        assert delegate.has_active_jobs
    finally:
        delegate._preview.shutdown(); delegate._jobs.shutdown()
