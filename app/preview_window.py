"""Bewusst aufgerufener nativer Bildvergleich mit gemeinsamem Ausschnitt."""
import math
from pathlib import Path

import AppKit as A
import Foundation as F
import objc

import jpegdrop
from app.profiles import ProfileStore, active_profile, apply_profile


class ComparisonView(A.NSView):
    def initWithFrame_(self, frame):
        self = objc.super(ComparisonView, self).initWithFrame_(frame)
        if self is not None:
            self.images = ()
            self.pixel_size = (1, 1)
            self.center = (.5, .5)
            self.fit = True
            self.zoom = 1.
            self.background = None
            self.on_zoom = None
        return self

    @objc.python_method
    def scale(self):
        bounds = self.bounds().size
        if self.fit:
            return min(max(1, (bounds.width - 36) / 2) / self.pixel_size[0],
                       max(1, bounds.height - 24) / self.pixel_size[1])
        return self.zoom / (self.window().backingScaleFactor() if self.window() else 1)

    @objc.python_method
    def set_images(self, before, after, size):
        self.images = (before, after) if before is not None and after is not None else ()
        self.pixel_size = size
        self.setNeedsDisplay_(True)

    def drawRect_(self, dirty):
        bounds = self.bounds()
        A.NSColor.windowBackgroundColor().setFill()
        A.NSRectFill(bounds)
        if not self.images:
            return
        pane_width = (bounds.size.width - 36) / 2
        scale = self.scale()
        width, height = (n * scale for n in self.pixel_size)
        for index, img in enumerate(self.images):
            pane = A.NSMakeRect(12 + index * (pane_width + 12), 12, pane_width, bounds.size.height - 24)
            A.NSGraphicsContext.saveGraphicsState()
            A.NSBezierPath.bezierPathWithRect_(pane).addClip()
            if self.background is not None:
                self.background.setFill(); A.NSRectFill(pane)
            else:
                for row in range(math.ceil(pane.size.height / 12)):
                    for col in range(math.ceil(pane.size.width / 12)):
                        A.NSColor.colorWithCalibratedWhite_alpha_(.74 if (row + col) % 2 else .9, 1).setFill()
                        A.NSRectFill(A.NSMakeRect(pane.origin.x + col * 12, pane.origin.y + row * 12, 12, 12))
            destination = A.NSMakeRect(pane.origin.x + pane_width / 2 - self.center[0] * width,
                                      pane.origin.y + pane.size.height / 2 - self.center[1] * height, width, height)
            backing = self.window().backingScaleFactor() if self.window() else 1
            pixel_scale = width * backing / img.size().width
            interpolation = A.NSImageInterpolationNone if not self.fit and pixel_scale >= 1 else A.NSImageInterpolationHigh
            A.NSGraphicsContext.currentContext().setImageInterpolation_(interpolation)
            img.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
                destination, A.NSZeroRect, A.NSCompositingOperationSourceOver, 1., False, None)
            A.NSGraphicsContext.restoreGraphicsState()

    def mouseDown_(self, event):
        self._last_drag = self.convertPoint_fromView_(event.locationInWindow(), None)

    def mouseDragged_(self, event):
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        scale = self.scale()
        self.center = tuple(max(0, min(1, c - delta / (size * scale))) for c, delta, size in
                            zip(self.center, (point.x - self._last_drag.x, point.y - self._last_drag.y), self.pixel_size))
        self._last_drag = point
        self.setNeedsDisplay_(True)

    def scrollWheel_(self, event):
        scale = self.scale()
        self.center = tuple(max(0, min(1, c + delta / (size * scale))) for c, delta, size in
                            zip(self.center, (event.scrollingDeltaX(), -event.scrollingDeltaY()), self.pixel_size))
        self.setNeedsDisplay_(True)


class PreviewWindowController(F.NSObject):
    def init(self):
        self = objc.super(PreviewWindowController, self).init()
        if self is not None:
            self.owner = None
            self.manager = None
            self._window = None
            self._source = None
            self._settings = None
            self._last_form = None
            self._displayed = None
            self._generation = 0
            self._panel = None
            self._profiles = ()
        return self

    @objc.python_method
    def show(self):
        if self._window is None:
            self._build()
        self._window.makeKeyAndOrderFront_(None)
        A.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        if self._source is None:
            self.chooseSource_(None)

    @objc.python_method
    def _build(self):
        win = A.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            A.NSMakeRect(0, 0, 1040, 720), A.NSWindowStyleMaskTitled | A.NSWindowStyleMaskClosable |
            A.NSWindowStyleMaskResizable | A.NSWindowStyleMaskMiniaturizable, A.NSBackingStoreBuffered, False)
        win.setTitle_("JPEGdrop Vergleichsvorschau")
        win.setReleasedWhenClosed_(False); win.setDelegate_(self)
        win.setContentMinSize_(A.NSMakeSize(880, 620))
        button = lambda title, action: A.NSButton.buttonWithTitle_target_action_(title, self, action)
        self._profile = A.NSPopUpButton.alloc().initWithFrame_pullsDown_(A.NSMakeRect(0, 0, 230, 26), False)
        self._profile.setAccessibilityLabel_("Vorschauprofil")
        self._profile.widthAnchor().constraintEqualToConstant_(230).setActive_(True)
        self._profile.setTarget_(self); self._profile.setAction_("profileChanged:")
        top = A.NSStackView.stackViewWithViews_([button("Bild wählen…", "chooseSource:"),
            A.NSTextField.labelWithString_("Profil:"), self._profile, button("Einstellungen…", "settings:"),
            button("Optionen übernehmen", "refreshOptions:")])
        top.setSpacing_(10)
        self._file = A.NSTextField.labelWithString_("Eine einzelne Bilddatei wählen.")
        self._file.setLineBreakMode_(A.NSLineBreakByTruncatingMiddle)
        self._file.setSelectable_(True)
        self._summary = A.NSTextField.wrappingLabelWithString_(
            "Profile hier wählen oder offene Formularwerte übernehmen. Es werden keine Einstellungen gespeichert.")
        self._summary.setTextColor_(A.NSColor.secondaryLabelColor())
        self._before = A.NSTextField.labelWithString_("Vorher · Quelle")
        self._after = A.NSTextField.labelWithString_("Nachher · echte Kodierung")
        headings = A.NSStackView.stackViewWithViews_([self._before, self._after])
        headings.setDistribution_(A.NSStackViewDistributionFillEqually); headings.setSpacing_(12)
        self._canvas = ComparisonView.alloc().initWithFrame_(A.NSMakeRect(0, 0, 1000, 400))
        self._canvas.setAccessibilityLabel_("Vorher und Nachher mit gemeinsamem Ausschnitt. Ziehen verschiebt beide Bilder.")
        self._canvas.heightAnchor().constraintGreaterThanOrEqualToConstant_(260).setActive_(True)
        self._zoom = A.NSTextField.labelWithString_("Einpassen")
        self._zoom.widthAnchor().constraintEqualToConstant_(125).setActive_(True)
        self._background = A.NSPopUpButton.alloc().initWithFrame_pullsDown_(A.NSMakeRect(0, 0, 180, 26), False)
        self._background.addItemsWithTitles_(["Schachbrett", "Weiß", "Dunkel", "Ausgabe-Hintergrund"])
        self._background.setAccessibilityLabel_("Vergleichshintergrund")
        self._background.setTarget_(self); self._background.setAction_("backgroundChanged:")
        tools = A.NSStackView.stackViewWithViews_([button("Einpassen", "fit:"), button("100 % Ausgabe", "actual:"),
            button("−", "zoomOut:"), button("+", "zoomIn:"), self._zoom, self._background])
        tools.setSpacing_(10)
        hint = A.NSTextField.labelWithString_(
            "Gleicher Ausschnitt · Ziehen verschiebt beide Bilder · Bei Skalierung wird die Quelle proportional dargestellt.")
        hint.setFont_(A.NSFont.systemFontOfSize_(11)); hint.setTextColor_(A.NSColor.secondaryLabelColor())
        self._status = A.NSTextField.wrappingLabelWithString_("Noch keine Vorschau.")
        self._status.heightAnchor().constraintGreaterThanOrEqualToConstant_(34).setActive_(True)
        self._status.setSelectable_(True)
        self._cancel = button("Berechnung abbrechen", "cancel:")
        self._retry = button("Neu berechnen", "recalculate:")
        self._convert = button("Mit diesen Optionen konvertieren", "convert:")
        self._convert.setEnabled_(False)
        bottom = A.NSStackView.stackViewWithViews_([self._cancel, self._retry,
            button("Schließen", "close:"), self._convert])
        bottom.setSpacing_(12)
        content = A.NSStackView.stackViewWithViews_([top, self._file, self._summary, headings,
            self._canvas, tools, hint, self._status, bottom])
        content.setOrientation_(A.NSUserInterfaceLayoutOrientationVertical)
        content.setAlignment_(A.NSLayoutAttributeLeading); content.setSpacing_(12)
        view = win.contentView(); view.addSubview_(content)
        content.setTranslatesAutoresizingMaskIntoConstraints_(False)
        for edge, offset in (("leading", 20), ("trailing", -20), ("top", 20), ("bottom", -20)):
            getattr(content, edge + "Anchor")().constraintEqualToAnchor_constant_(getattr(view, edge + "Anchor")(), offset).setActive_(True)
        for child in (self._file, self._summary, headings, self._canvas, self._status):
            child.widthAnchor().constraintEqualToAnchor_(content.widthAnchor()).setActive_(True)
        self._window = win
        self._refresh_profiles()
        win.center()

    @objc.python_method
    def _refresh_profiles(self):
        try:
            self._profiles = ProfileStore().list()
            problem = None
        except (ValueError, OSError) as error:
            self._profiles, problem = (), str(error)
        self._profile.removeAllItems()
        self._profile.addItemWithTitle_("Benutzerdefiniert")
        for profile in self._profiles:
            self._profile.addItemWithTitle_(f"„{profile.name}“")
        values = self._settings
        selected = active_profile(values, self._profiles) if values else None
        self._profile.selectItemAtIndex_(self._profiles.index(selected) + 1 if selected else 0)
        self._profile.setToolTip_(problem or "Profil gilt nur für diesen Vergleich und dessen Konvertierung.")

    @objc.python_method
    def _form(self):
        controller = self.owner._settings_controller
        if controller and controller._window and controller._window.isVisible():
            return controller._form_settings()
        return None

    @objc.python_method
    def _request(self, settings):
        self._settings = dict(settings)
        self._refresh_profiles()
        self._displayed = None
        self._convert.setEnabled_(False)
        self._canvas.set_images(None, None, (1, 1))
        if not self._source:
            return
        self._before.setStringValue_("Vorher · Quelle")
        self._after.setStringValue_("Nachher · echte Kodierung")
        self.backgroundChanged_(None)
        try:
            profile = active_profile(settings, self._profiles)
            self._generation = self.manager.request(self._source, settings, profile.name if profile else None)
            codec = jpegdrop.get_codec(settings["codec"])
            preset = jpegdrop.get_preset(codec, settings["preset"])
            edge = f"max. {settings['max_edge']} px" if settings["max_edge"] else "Originalmaße"
            limit = f" · max. {settings['target_bytes']} Byte" if settings["target_bytes"] else ""
            alpha = "Alpha erhalten" if settings["preserve_alpha"] else f"Hintergrund {settings['background']}"
            destination = {"beside": "neben der Quelle", "subfolder": f"Unterordner {settings['output_subdir']}",
                           "directory": settings["output_directory"]}[settings["output_mode"]]
            resize = (f" · weiter verkleinern bis {settings['minimum_target_edge']} px"
                      if settings["target_bytes"] and settings["allow_target_resize"] else "")
            self._summary.setStringValue_(f"{codec.label} · {preset.label} · {edge}{limit}{resize}\n{alpha} · Ausgabe: {destination}")
            self._status.setStringValue_("Vorschau wird berechnet … Quellen und Zielablage bleiben unverändert.")
        except (ValueError, OSError) as error:
            self.manager.cancel()
            self._status.setStringValue_(str(error))

    def chooseSource_(self, sender):
        if self._panel is not None:
            return
        panel = A.NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(True); panel.setCanChooseDirectories_(False); panel.setAllowsMultipleSelection_(False)
        panel.setMessage_("Ein Bild für den Vorher-/Nachher-Vergleich wählen.")
        self._panel = panel
        def finished(response):
            self._panel = None
            if response == A.NSModalResponseOK:
                self._source = str(panel.URL().path())
                self._file.setStringValue_(Path(self._source).name); self._file.setToolTip_(self._source)
                self.fit_(None)
                self.refreshOptions_(None)
        panel.beginSheetModalForWindow_completionHandler_(self._window, finished)

    def refreshOptions_(self, sender):
        try:
            form = self._form()
            self._last_form = form
            self._request(form or jpegdrop.load_settings())
        except (ValueError, OSError) as error:
            self.manager.cancel(); self._convert.setEnabled_(False)
            self._status.setStringValue_(str(error))

    def settings_(self, sender):
        self.owner.showSettings_(None)

    def profileChanged_(self, sender):
        index = self._profile.indexOfSelectedItem() - 1
        if index < 0 and self._settings:
            self._request({key: value for key, value in self._settings.items() if key != "profile_id"})
            return
        if 0 <= index < len(self._profiles):
            selected = self._profiles[index]
            try:
                if selected not in ProfileStore().list():
                    raise ValueError("Das Profil wurde inzwischen geändert. Bitte erneut wählen.")
                self._request(apply_profile(self._settings or jpegdrop.load_settings(), selected))
            except (ValueError, OSError) as error:
                self.manager.cancel(); self._convert.setEnabled_(False); self._status.setStringValue_(str(error))

    @objc.python_method
    def poll(self):
        if self._window is None or not self._window.isVisible():
            return
        try:
            form = self._form()
            if form is not None and form != self._last_form:
                self._last_form = dict(form)
                self._request(form)
        except (ValueError, OSError) as error:
            if self._last_form != str(error):
                self.manager.cancel()
            self._last_form = str(error)
            self._convert.setEnabled_(False)
            self._canvas.set_images(None, None, (1, 1)); self._status.setStringValue_(str(error))
            return
        state = self.manager.snapshot()
        marker = state["generation"], state["status"]
        if marker == self._displayed:
            return
        self._displayed = marker
        self._cancel.setEnabled_(state["status"] == "running")
        self._convert.setEnabled_(False)
        if state["status"] == "ready":
            try:
                images = [A.NSImage.alloc().initWithData_(F.NSData.dataWithContentsOfFile_(state[key])) for key in ("before", "after")]
                if any(img is None for img in images):
                    raise ValueError("Die Vergleichsbilder konnten nicht geladen werden.")
                result = state["result"]
                self._canvas.set_images(*images, (result["output_width"], result["output_height"]))
                self._before.setStringValue_(f"Vorher · {result['src_bytes']} Byte · {result['source_width']} × {result['source_height']} px")
                self._after.setStringValue_(f"Nachher · {result['out_bytes']} Byte · {result['output_width']} × {result['output_height']} px")
                warnings = " ".join(w["message"] for w in result["warnings"])
                self._status.setStringValue_(warnings or "Vorschau bereit. Konvertieren legt eine kollisionssichere Kopie mit genau diesen Optionen an.")
                self._convert.setEnabled_(True)
            except (ValueError, OSError) as error:
                self._status.setStringValue_(str(error))
        elif state["status"] in {"cancelled", "error"}:
            self._canvas.set_images(None, None, (1, 1))
            self._status.setStringValue_(state.get("error") or "Vorschau abgebrochen. Keine Ausgabe angelegt.")

    def convert_(self, sender):
        # Auch zwischen zwei Timer-Ticks darf kein überholter Formularstand starten.
        self.poll()
        try:
            self.manager.convert(self._generation, self.owner._jobs)
            self._status.setStringValue_("Auftrag mit den betrachteten Optionen eingereiht. Weitere Drops nutzen weiterhin die gespeicherten Einstellungen.")
            self.owner.showJobs_(None)
        except (ValueError, OSError) as error:
            self._status.setStringValue_(str(error)); self._convert.setEnabled_(False)

    def recalculate_(self, sender):
        if self._settings:
            self._request(self._settings)

    def cancel_(self, sender):
        self.manager.cancel(); self.poll()

    def close_(self, sender):
        self._window.performClose_(None)

    def windowWillClose_(self, notification):
        if self._panel is not None:
            self._panel.cancel_(None)
        self.manager.cancel()
        self._canvas.set_images(None, None, (1, 1))
        self._source = None

    def fit_(self, sender):
        self._canvas.fit = True; self._canvas.center = (.5, .5)
        self._zoom.setStringValue_("Einpassen"); self._canvas.setNeedsDisplay_(True)

    def actual_(self, sender):
        self._set_zoom(1.)

    @objc.python_method
    def _set_zoom(self, zoom):
        self._canvas.fit = False; self._canvas.zoom = max(.0625, min(8, zoom))
        self._zoom.setStringValue_(f"{self._canvas.zoom * 100:g} % Ausgabe")
        self._canvas.setNeedsDisplay_(True)

    def zoomIn_(self, sender):
        self._set_zoom(1. if self._canvas.fit else self._canvas.zoom * 2)

    def zoomOut_(self, sender):
        self._set_zoom(.5 if self._canvas.fit else self._canvas.zoom / 2)

    def backgroundChanged_(self, sender):
        index = self._background.indexOfSelectedItem()
        color = (self._settings or jpegdrop.DEFAULT_SETTINGS)["background"]
        self._canvas.background = (None, A.NSColor.whiteColor(), A.NSColor.colorWithCalibratedWhite_alpha_(.15, 1),
            A.NSColor.colorWithSRGBRed_green_blue_alpha_(*(int(color[i:i+2], 16) / 255 for i in (1, 3, 5)), 1))[index]
        self._canvas.setNeedsDisplay_(True)
