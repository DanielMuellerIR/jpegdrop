#!/usr/bin/env python3
"""JPEGdrop — macOS-Dock-Droplet (PyObjC).

Reguläre App MIT sichtbarem Dock-Icon: Bilder aufs Dock-Icon ziehen → sie werden
mit dem aktuell gewählten Codec/Preset neben der Quelle abgelegt (Ergebnisansicht
und Notification am Ende). Codec/Preset wählt man über das "JD"-Kürzel in der Menüleiste ODER per
Rechtsklick aufs Dock-Icon (applicationDockMenu); "Einstellungen…" (⌘,) öffnet ein
natives Fenster. Settings liegen in
~/Library/Application Support/JPEGdrop/settings.json.

Drops kommen auf aktuellem macOS über `application:openURLs:` (das alte
`application:openFiles:` ist deprecated und feuert dort nicht mehr zuverlässig).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

# Cocoa-Stack -----------------------------------------------------------
import objc
from Cocoa import (
    NSApplication,
    NSApplicationActivationPolicyRegular,
    NSBackingStoreBuffered,
    NSButton,
    NSMakeRect,
    NSMenu,
    NSMenuItem,
    NSObject,
    NSPopUpButton,
    NSStackView,
    NSStatusBar,
    NSTextField,
    NSUserInterfaceLayoutOrientationVertical,
    NSWindow,
    NSWindowStyleMaskTitled,
    NSWindowStyleMaskClosable,
)
import AppKit
import Foundation

# Globale NSApp-Referenz — in den AppDelegate-Methoden brauchen wir sie.
# Wird in main() initialisiert.
NSApp = None  # type: ignore
_DELEGATE = None  # starke Referenz aufs AppDelegate (NSApp haelt es nur schwach)

# Eigener Code (für Settings / Codec-Liste) -----------------------------
# jpegdrop.py liegt immer im Ordner über diesem — im Repo ebenso wie im
# fertigen App-Bundle (dort: Contents/Resources/app/menu_app.py neben
# Contents/Resources/jpegdrop.py). Deshalb reicht ein relativer Verweis.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import jpegdrop  # type: ignore  # noqa: E402
from app.jobs import JobManager
from app.jobs_window import JobsWindowController
from app.updates import JDUpdateController
from app.input_plan import IMAGE_EXTENSIONS, InventoryOptions


# Interpreter und Skript für die eigentliche Konvertierung (läuft als eigener
# Prozess, damit ein Fehler dort die App nicht mitreißt).
# Im Bundle setzt der Launcher in Contents/MacOS beide Variablen auf den
# mitgelieferten Interpreter. Ohne sie — also beim direkten Start aus dem Repo
# heraus — gilt das dortige .venv.
VENV_PY = os.environ.get("JPEGDROP_VENV_PY", str(HERE.parent / ".venv" / "bin" / "python"))
JPEGDROP_SCRIPT = os.environ.get("JPEGDROP_SCRIPT", str(HERE.parent / "jpegdrop.py"))


import time as _time
_DBG = os.path.expanduser("~/Library/Logs/jpegdrop-debug.log")


def _dbg(msg: str) -> None:
    """Breadcrumb-Log zur Diagnose — NUR aktiv, wenn JPEGDROP_DEBUG gesetzt ist.
    Standardmäßig still (committierbar, kein Cruft bei jedem Start). Fail-open."""
    if not os.environ.get("JPEGDROP_DEBUG"):
        return
    try:
        with open(_DBG, "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (_time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass


_dbg("=== menu_app.py importiert (pid %d) ===" % os.getpid())

# Akzeptierte Eingabe-Bildformate. Dient als Filter
# gegen versehentlich gedroppte Nicht-Bilder und gegen die Start-openFiles-Falle.
IMAGE_EXTS = IMAGE_EXTENSIONS


# ===========================================================================
# Benachrichtigungen
# ===========================================================================
def show_notification(title: str, body: str, success: bool = True, *, subtitle: str | None = None) -> None:
    """Zeigt eine native macOS-Benachrichtigung über osascript (UserNotificationCenter
    würde mehr PyObjC-Bindings brauchen; osascript reicht und funktioniert immer)."""
    # title UND body escapen: Anführungszeichen im AppleScript-String brechen
    # die Syntax und öffnen sonst eine Code-Injection-Stelle in osascript.
    # Reihenfolge ist wichtig: ERST den Backslash verdoppeln, DANN die
    # Anführungszeichen escapen. Sonst würde ein Backslash im Text (z.B. aus
    # einem Dateinamen) das gerade eingefügte \" wieder zerbrechen.
    def _escape(s: str) -> str:
        return s.replace('\\', '\\\\').replace('"', '\\"')
    safe_subtitle = _escape(subtitle if subtitle is not None else ("fertig" if success else "Fehler"))
    safe_title = _escape(title)
    safe_body  = _escape(body)
    script = f'display notification "{safe_body}" with title "{safe_title}" subtitle "{safe_subtitle}"'
    try:
        subprocess.Popen(["osascript", "-e", script])
    except Exception:
        pass


# ===========================================================================
# Settings-Window
# ===========================================================================
class SettingsWindowController(NSObject):
    """Controller für das ⌘,-Einstellungen-Fenster."""

    def init(self):
        self = objc.super(SettingsWindowController, self).init()
        if self is None:
            return None
        self._window = None
        self._codec_popup = None
        self._preset_popup = None
        self._update_controller = None
        self._automatic_checks = None
        self._update_info = None
        self._max_edge = None
        self._preserve_alpha = None
        self._background = None
        self._image_info = None
        self._validation_error = None
        self._recursive = None
        self._output_mode = None
        self._output_subdir = None
        self._output_path = None
        self._output_choose = None
        self._output_directory_value = ""
        self._folder_panel = None
        self._target_size = None
        self._target_resize = None
        self._target_minimum = None
        # Interne Codec-Namen, Index-gekoppelt zu den Popup-Labels (s. _populate).
        self._codec_names = None
        # Callback des AppDelegate: wird nach erfolgreichem Speichern gerufen,
        # damit Statuszeile + Preset-Häkchen im JD-Menü sofort nachziehen.
        self._on_saved = None
        return self

    def show(self):
        self._populate_from_settings()
        self._window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    def _populate_from_settings(self):
        """Fenster bauen (falls nötig) und Popups aus den Settings befüllen.
        Getrennt von show(), damit die Logik headless testbar ist."""
        if self._window is None:
            self._build_window()
        s = jpegdrop.load_settings()
        # Popup zeigt die Anzeige-Labels ("JPEG", "WebP", …); die internen
        # Namen laufen Index-gekoppelt in self._codec_names mit.
        self._codec_names = list(jpegdrop.CODECS.keys())
        labels = [jpegdrop.CODECS[n].label for n in self._codec_names]
        self._codec_popup.removeAllItems()
        self._codec_popup.addItemsWithTitles_(labels)
        try:
            idx = self._codec_names.index(s["codec"])
        except ValueError:
            idx = 0
        self._codec_popup.selectItemAtIndex_(idx)
        # _refresh_presets füllt das Preset-Popup UND wählt das gespeicherte
        # Preset direkt aus — keine zweite Auswahl-Schleife nötig.
        self._refresh_presets(None)
        self._max_edge.setStringValue_(str(s["max_edge"]))
        self._target_size.setStringValue_(str(Decimal(s["target_bytes"]) / 1000).replace(".", ","))
        self._target_resize.setState_(AppKit.NSControlStateValueOn if s["allow_target_resize"] else AppKit.NSControlStateValueOff)
        self._target_minimum.setStringValue_(str(s["minimum_target_edge"]))
        self._refresh_target_options()
        self._preserve_alpha.setState_(AppKit.NSControlStateValueOn if s["preserve_alpha"] else AppKit.NSControlStateValueOff)
        color = s["background"]
        self._background.setColor_(AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(
            int(color[1:3], 16) / 255, int(color[3:5], 16) / 255, int(color[5:7], 16) / 255, 1))
        self._validation_error.setHidden_(True)
        self._refresh_image_options()
        self._recursive.setState_(AppKit.NSControlStateValueOn if s["recursive"] else AppKit.NSControlStateValueOff)
        self._output_mode.selectItemAtIndex_(["beside", "subfolder", "directory"].index(s["output_mode"]))
        self._output_subdir.setStringValue_(s["output_subdir"])
        self._output_directory_value = s["output_directory"]
        self._refresh_output_options()
        updates = self._update_controller
        available = updates is not None and updates.available
        self._automatic_checks.setEnabled_(available)
        self._automatic_checks.setState_(AppKit.NSControlStateValueOn if available and updates.automatic_checks
                                         else AppKit.NSControlStateValueOff)
        self._update_info.setStringValue_(
            "Die Suche lädt den Update-Feed. Installiert wird nach Ihrer Bestätigung, "
            "sobald alle aktiven Aufträge fertig sind."
            if available else (updates.problem if updates else "Updates sind nicht verfügbar."))
        self._window.setContentSize_(self._window.contentView().fittingSize())

    def _selected_codec_name(self):
        """Interner Codec-Name ("jpeg", …) des gewählten Popup-Eintrags."""
        i = self._codec_popup.indexOfSelectedItem()
        if self._codec_names is None or not (0 <= i < len(self._codec_names)):
            return None
        return self._codec_names[i]

    def _build_window(self):
        # Fenster: Titel + Schließen-Knopf (Settings-üblich, kein Minimieren).
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 480, 220),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
            NSBackingStoreBuffered,
            False,
        )
        win.setTitle_("JPEGdrop Einstellungen")
        # WICHTIG: sonst gibt AppKit das Fenster beim roten Schließen-Knopf
        # frei und der nächste ⌘,-Aufruf greift auf ein freigegebenes Objekt.
        win.setReleasedWhenClosed_(False)

        # --- Formular: Labels + Popups sauber ausgerichtet via NSGridView ---
        codec_label = NSTextField.labelWithString_("Ziel-Codec:")
        codec_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(0, 0, 200, 25), False
        )
        codec_popup.setAccessibilityLabel_("Ziel-Codec")
        preset_label = NSTextField.labelWithString_("Qualitäts-Preset:")
        preset_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(0, 0, 320, 25), False
        )
        preset_popup.setAccessibilityLabel_("Qualitäts-Preset")
        grid = AppKit.NSGridView.gridViewWithViews_(
            [[codec_label, codec_popup], [preset_label, preset_popup]]
        )
        grid.setRowSpacing_(12)
        grid.setColumnSpacing_(10)
        # Labels rechtsbündig vor den Controls (macOS-Formular-Standard)
        grid.columnAtIndex_(0).setXPlacement_(AppKit.NSGridCellPlacementTrailing)
        # Popups nicht unter Minimalbreite fallen lassen (lange Preset-Titel)
        codec_popup.widthAnchor().constraintGreaterThanOrEqualToConstant_(160).setActive_(True)
        preset_popup.widthAnchor().constraintGreaterThanOrEqualToConstant_(300).setActive_(True)

        # --- Hinweistext ---
        info = NSTextField.wrappingLabelWithString_(
            "Diese Einstellungen werden gespeichert und gelten ab dem nächsten "
            "Bild-Drop. Codec + Preset bestimmen das Ausgabeformat und die "
            "Komprimierung der Kopien."
        )
        info.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        info.setFont_(AppKit.NSFont.systemFontOfSize_(AppKit.NSFont.smallSystemFontSize()))
        info.setPreferredMaxLayoutWidth_(440)

        image_separator = AppKit.NSBox.alloc().init()
        image_separator.setBoxType_(AppKit.NSBoxSeparator)
        max_edge = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 120, 24))
        max_edge.setAccessibilityLabel_("Maximale Bildkante")
        max_edge.widthAnchor().constraintEqualToConstant_(120).setActive_(True)
        edge_row = NSStackView.stackViewWithViews_([
            max_edge, NSTextField.labelWithString_("Pixel · 0 = Originalgröße")])
        edge_row.setSpacing_(10)
        preserve = NSButton.checkboxWithTitle_target_action_("Transparenz erhalten", self, "imageOptionsChanged:")
        background = AppKit.NSColorWell.alloc().initWithFrame_(NSMakeRect(0, 0, 60, 26))
        background.setAccessibilityLabel_("Hintergrundfarbe")
        background.widthAnchor().constraintEqualToConstant_(60).setActive_(True)
        background.heightAnchor().constraintEqualToConstant_(26).setActive_(True)
        target_size = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 120, 24))
        target_size.setAccessibilityLabel_("Zielgröße in KB")
        target_size.widthAnchor().constraintEqualToConstant_(120).setActive_(True)
        size_row = NSStackView.stackViewWithViews_([target_size, NSTextField.labelWithString_("KB · 1000 Byte · 0 = aus")])
        size_row.setSpacing_(10)
        target_minimum = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 80, 24))
        target_minimum.setAccessibilityLabel_("Mindestkante für Zielgröße")
        target_minimum.widthAnchor().constraintEqualToConstant_(80).setActive_(True)
        minimum_row = NSStackView.stackViewWithViews_([target_minimum, NSTextField.labelWithString_("Pixel · nur bei Größenlimit")])
        minimum_row.setSpacing_(10)
        target_resize = NSButton.checkboxWithTitle_target_action_(
            "Für Zielgröße weiter verkleinern", self, "targetOptionsChanged:")
        image_grid = AppKit.NSGridView.gridViewWithViews_([
            [NSTextField.labelWithString_("Längere Kante:"), edge_row],
            [NSTextField.labelWithString_("Transparenz:"), preserve],
            [NSTextField.labelWithString_("Hintergrund:"), background],
            [NSTextField.labelWithString_("Zielgröße:"), size_row],
            [NSTextField.labelWithString_("Mindestkante:"), minimum_row],
        ])
        image_grid.setRowSpacing_(10)
        image_grid.setColumnSpacing_(10)
        image_grid.columnAtIndex_(0).setXPlacement_(AppKit.NSGridCellPlacementTrailing)
        image_info = NSTextField.wrappingLabelWithString_("")
        image_info.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        image_info.setFont_(AppKit.NSFont.systemFontOfSize_(AppKit.NSFont.smallSystemFontSize()))
        image_info.setPreferredMaxLayoutWidth_(440)
        validation = NSTextField.wrappingLabelWithString_("")
        validation.setTextColor_(AppKit.NSColor.systemRedColor())
        validation.setPreferredMaxLayoutWidth_(440)
        validation.setHidden_(True)

        output_separator = AppKit.NSBox.alloc().init()
        output_separator.setBoxType_(AppKit.NSBoxSeparator)
        recursive = NSButton.checkboxWithTitle_target_action_("Unterordner einbeziehen", None, None)
        output_mode = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 0, 300, 25), False)
        output_mode.addItemsWithTitles_(["Neben der Quelle", "Ausgabe-Unterordner", "Gewählter Zielordner"])
        output_mode.setAccessibilityLabel_("Ausgabeablage")
        output_mode.setTarget_(self); output_mode.setAction_("outputModeChanged:")
        output_mode.widthAnchor().constraintGreaterThanOrEqualToConstant_(300).setActive_(True)
        output_subdir = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 200, 24))
        output_subdir.setAccessibilityLabel_("Ausgabe-Unterordner")
        output_subdir.widthAnchor().constraintEqualToConstant_(200).setActive_(True)
        output_path = NSTextField.labelWithString_("Kein Ordner gewählt")
        output_path.setLineBreakMode_(AppKit.NSLineBreakByTruncatingMiddle)
        output_path.widthAnchor().constraintEqualToConstant_(210).setActive_(True)
        output_path.setAccessibilityLabel_("Gewählter Zielordner")
        choose = NSButton.buttonWithTitle_target_action_("Ordner wählen…", self, "chooseOutputDirectory:")
        path_row = NSStackView.stackViewWithViews_([output_path, choose])
        path_row.setSpacing_(10)
        output_grid = AppKit.NSGridView.gridViewWithViews_([
            [NSTextField.labelWithString_("Ausgabe:"), output_mode],
            [NSTextField.labelWithString_("Unterordner:"), output_subdir],
            [NSTextField.labelWithString_("Zielordner:"), path_row],
        ])
        output_grid.setRowSpacing_(10); output_grid.setColumnSpacing_(10)
        output_grid.columnAtIndex_(0).setXPlacement_(AppKit.NSGridCellPlacementTrailing)
        output_info = NSTextField.wrappingLabelWithString_(
            "Ordner werden vor der Konvertierung eingelesen. In einer anderen Ablage "
            "bleibt ihre Unterstruktur erhalten; vorhandene Dateien bleiben unverändert.")
        output_info.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        output_info.setFont_(AppKit.NSFont.systemFontOfSize_(AppKit.NSFont.smallSystemFontSize()))
        output_info.setPreferredMaxLayoutWidth_(440)

        separator = AppKit.NSBox.alloc().init()
        separator.setBoxType_(AppKit.NSBoxSeparator)
        automatic = NSButton.checkboxWithTitle_target_action_("Automatisch nach Updates suchen", None, None)
        update_info = NSTextField.wrappingLabelWithString_("")
        update_info.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        update_info.setFont_(AppKit.NSFont.systemFontOfSize_(AppKit.NSFont.smallSystemFontSize()))
        update_info.setPreferredMaxLayoutWidth_(440)

        # --- Buttons rechtsbündig; Speichern = Default (Enter), Abbrechen = Esc ---
        cancel_btn = NSButton.buttonWithTitle_target_action_("Abbrechen", self, "close:")
        cancel_btn.setKeyEquivalent_(chr(27))  # Escape
        save_btn = NSButton.buttonWithTitle_target_action_("Speichern", self, "saveAndClose:")
        save_btn.setKeyEquivalent_("\r")       # Enter -> blauer Default-Button
        button_row = NSStackView.alloc().init()
        button_row.setOrientation_(0)  # horizontal
        button_row.setSpacing_(12)
        # Gravity-Konstanten: Leading=1, Center=2, Trailing=3. (Die frühere
        # 1<<1-Schreibweise war in Wahrheit CENTER — daher das schiefe Layout.)
        button_row.addView_inGravity_(cancel_btn, 3)
        button_row.addView_inGravity_(save_btn, 3)

        # --- Vertikaler Gesamt-Stack, an die Fensterränder gepinnt ---
        content = NSStackView.alloc().init()
        content.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
        content.setAlignment_(AppKit.NSLayoutAttributeLeading)
        content.setSpacing_(16)
        content.addArrangedSubview_(grid)
        content.addArrangedSubview_(info)
        content.addArrangedSubview_(image_separator)
        content.addArrangedSubview_(image_grid)
        content.addArrangedSubview_(target_resize)
        content.addArrangedSubview_(image_info)
        content.addArrangedSubview_(output_separator)
        content.addArrangedSubview_(recursive)
        content.addArrangedSubview_(output_grid)
        content.addArrangedSubview_(output_info)
        content.addArrangedSubview_(separator)
        content.addArrangedSubview_(automatic)
        content.addArrangedSubview_(update_info)
        content.addArrangedSubview_(validation)
        content.addArrangedSubview_(button_row)

        cv = win.contentView()
        cv.addSubview_(content)
        content.setTranslatesAutoresizingMaskIntoConstraints_(False)
        # PyObjC-Falle: die ObjC-Property "active" ist hier KEIN Python-Attribut —
        # ".active = True" wirft AttributeError (Fenster crashte beim Öffnen).
        # Richtig ist der Setter setActive_().
        content.leadingAnchor().constraintEqualToAnchor_constant_(cv.leadingAnchor(), 20).setActive_(True)
        content.trailingAnchor().constraintEqualToAnchor_constant_(cv.trailingAnchor(), -20).setActive_(True)
        content.topAnchor().constraintEqualToAnchor_constant_(cv.topAnchor(), 20).setActive_(True)
        content.bottomAnchor().constraintEqualToAnchor_constant_(cv.bottomAnchor(), -20).setActive_(True)
        # Hinweistext + Button-Zeile über die volle Breite (Buttons dadurch rechts)
        info.widthAnchor().constraintEqualToAnchor_(content.widthAnchor()).setActive_(True)
        separator.widthAnchor().constraintEqualToAnchor_(content.widthAnchor()).setActive_(True)
        update_info.widthAnchor().constraintEqualToAnchor_(content.widthAnchor()).setActive_(True)
        button_row.widthAnchor().constraintEqualToAnchor_(content.widthAnchor()).setActive_(True)
        for view in (image_separator, image_info, validation, output_separator, output_info):
            view.widthAnchor().constraintEqualToAnchor_(content.widthAnchor()).setActive_(True)

        # Fensterhöhe aus dem Inhalt ableiten statt fixer Leere darunter.
        win.setContentSize_(cv.fittingSize())
        win.center()

        # Callbacks
        codec_popup.setTarget_(self)
        codec_popup.setAction_("codecChanged:")

        self._window = win
        self._codec_popup = codec_popup
        self._preset_popup = preset_popup
        self._automatic_checks = automatic
        self._update_info = update_info
        self._max_edge = max_edge
        self._preserve_alpha = preserve
        self._background = background
        self._image_info = image_info
        self._target_size = target_size
        self._target_resize = target_resize
        self._target_minimum = target_minimum
        self._validation_error = validation
        self._recursive = recursive
        self._output_mode = output_mode
        self._output_subdir = output_subdir
        self._output_path = output_path
        self._output_choose = choose

    # Objective-C Selectors -------------------------------------------------
    def codecChanged_(self, sender):
        self._refresh_presets(sender)
        self._refresh_image_options()

    def imageOptionsChanged_(self, sender):
        self._refresh_image_options()

    def targetOptionsChanged_(self, sender):
        self._refresh_target_options()

    def _refresh_target_options(self):
        active = self._target_resize.state() == AppKit.NSControlStateValueOn
        self._target_minimum.setEnabled_(active)
        if not active:
            value = self._target_minimum.stringValue().strip()
            if not value.isascii() or not value.isdecimal() or int(value) < 1:
                self._target_minimum.setStringValue_(str(jpegdrop.load_settings()["minimum_target_edge"]))

    def outputModeChanged_(self, sender):
        self._refresh_output_options()

    def _refresh_output_options(self):
        mode = ["beside", "subfolder", "directory"][self._output_mode.indexOfSelectedItem()]
        if mode != "subfolder":
            try:
                InventoryOptions(output_subdir=self._output_subdir.stringValue().strip())
            except ValueError:
                self._output_subdir.setStringValue_(jpegdrop.load_settings()["output_subdir"])
        self._output_subdir.setEnabled_(mode == "subfolder")
        self._output_choose.setEnabled_(mode == "directory")
        path = self._output_directory_value
        home = str(Path.home())
        display = "~" + path[len(home):] if path == home or path.startswith(home + "/") else path
        self._output_path.setStringValue_(display or "Kein Ordner gewählt")
        self._output_path.setToolTip_(path or "Bitte einen Zielordner wählen.")
        self._output_path.setTextColor_(AppKit.NSColor.labelColor() if mode == "directory"
                                       else AppKit.NSColor.secondaryLabelColor())
        self._window.setContentSize_(self._window.contentView().fittingSize())

    def chooseOutputDirectory_(self, sender):
        panel = AppKit.NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(False); panel.setCanChooseDirectories_(True)
        panel.setAllowsMultipleSelection_(False); panel.setCanCreateDirectories_(True)
        panel.setPrompt_("Auswählen")
        if self._output_directory_value:
            panel.setDirectoryURL_(Foundation.NSURL.fileURLWithPath_(self._output_directory_value))
        self._folder_panel = panel

        def completed(response):
            if response == AppKit.NSModalResponseOK and panel.URL() is not None:
                self._output_directory_value = str(panel.URL().path())
                self._refresh_output_options()
            self._folder_panel = None

        panel.beginSheetModalForWindow_completionHandler_(self._window, completed)

    def _refresh_image_options(self):
        jpeg = self._selected_codec_name() == "jpeg"
        self._preserve_alpha.setEnabled_(not jpeg)
        if jpeg:
            self._preserve_alpha.setState_(AppKit.NSControlStateValueOff)
        preserved = self._preserve_alpha.state() == AppKit.NSControlStateValueOn
        self._background.setEnabled_(not preserved)
        alpha_info = (
            "JPEG entfernt Transparenz mit der gewählten Hintergrundfarbe. Bilder werden niemals vergrößert."
            if jpeg else "Transparenz optional erhalten; sonst wird die Hintergrundfarbe verwendet. Bilder werden niemals vergrößert.")
        self._image_info.setStringValue_(alpha_info + " Größenlimits senken die Qualität bis q15; verlustfreie Presets bleiben verlustfrei.")
        self._window.setContentSize_(self._window.contentView().fittingSize())

    def _refresh_presets(self, _sender):
        codec_name = self._selected_codec_name()
        if not codec_name:
            return
        codec = jpegdrop.CODECS.get(codec_name)
        if not codec:
            return
        # Preset-Popup füllen mit "name — label"
        self._preset_popup.removeAllItems()
        for p in codec.presets:
            self._preset_popup.addItemWithTitle_(f"{p.name} — {p.label}")
            self._preset_popup.lastItem().setToolTip_(p.description)
        # Gespeichertes Preset beibehalten, sonst den Standard des neuen Codecs wählen.
        s = jpegdrop.load_settings()
        names = [p.name for p in codec.presets]
        target_preset = s["preset"] if s["preset"] in names else codec.default_preset
        self._preset_popup.selectItemAtIndex_(names.index(target_preset))

    def saveAndClose_(self, _sender):
        codec_name = self._selected_codec_name()
        preset_title = self._preset_popup.titleOfSelectedItem()
        if codec_name and preset_title:
            # "name — label" -> "name"
            preset_name = preset_title.split(" — ", 1)[0].strip()
            try:
                edge = self._max_edge.stringValue().strip()
                if not edge.isascii() or not edge.isdecimal():
                    raise ValueError("Die maximale Bildkante muss eine ganze Zahl ab 0 sein.")
                color = self._background.color().colorUsingColorSpace_(AppKit.NSColorSpace.sRGBColorSpace())
                background = "#" + "".join(f"{round(max(0, min(1, c)) * 255):02X}"
                    for c in (color.redComponent(), color.greenComponent(), color.blueComponent()))
                minimum = self._target_minimum.stringValue().strip()
                if not minimum.isascii() or not minimum.isdecimal() or int(minimum) < 1:
                    raise ValueError("Die Mindestkante muss eine positive ganze Pixelzahl sein.")
                options = jpegdrop.ImageOptions(int(edge),
                    self._preserve_alpha.state() == AppKit.NSControlStateValueOn, background,
                    target_bytes=jpegdrop.parse_target_size(self._target_size.stringValue() + "KB"),
                    allow_target_resize=self._target_resize.state() == AppKit.NSControlStateValueOn,
                    minimum_target_edge=int(minimum))
                inventory = InventoryOptions(
                    recursive=self._recursive.state() == AppKit.NSControlStateValueOn,
                    output_mode=["beside", "subfolder", "directory"][self._output_mode.indexOfSelectedItem()],
                    output_directory=self._output_directory_value,
                    output_subdir=self._output_subdir.stringValue().strip(),
                )
                jpegdrop.save_settings({"codec": codec_name, "preset": preset_name,
                    "max_edge": options.max_edge, "preserve_alpha": options.preserve_alpha,
                    "background": options.background, "recursive": inventory.recursive,
                    "target_bytes": options.target_bytes, "allow_target_resize": options.allow_target_resize,
                    "minimum_target_edge": options.minimum_target_edge,
                    "output_mode": inventory.output_mode, "output_directory": inventory.output_directory,
                    "output_subdir": inventory.output_subdir})
            except (ValueError, OSError) as error:
                self._validation_error.setStringValue_(str(error))
                self._validation_error.setHidden_(False)
                self._window.setContentSize_(self._window.contentView().fittingSize())
                return
            if self._update_controller is not None:
                self._update_controller.set_automatic_checks(
                    self._automatic_checks.state() == AppKit.NSControlStateValueOn)
            # AppDelegate informieren: JD-Menü (Statuszeile, Häkchen) zeigte
            # sonst bis zum nächsten Menü-Klick die alten Werte.
            if self._on_saved is not None:
                self._on_saved()
        self._window.orderOut_(None)

    def close_(self, _sender):
        self._window.orderOut_(None)


# ===========================================================================
# AppDelegate
# ===========================================================================
class AppDelegate(NSObject):
    """Hält Status-Item + Menü + Settings-Window. Wird beim App-Start geladen."""

    def init(self):
        self = objc.super(AppDelegate, self).init()
        if self is None:
            return None
        self._status_item = None
        self._settings_controller = None
        self._jobs_controller = None
        self._jobs = JobManager(VENV_PY, JPEGDROP_SCRIPT)
        self._jobs_revision = -1
        self._notified_jobs = set()
        self._terminating = False
        self._external_stop = False
        self._stop_scheduled = False
        self._quit_dialog_open = False
        self._updates = JDUpdateController.alloc().init()
        self._updates.can_relaunch = lambda: not self._quit_dialog_open and not self._terminating
        self._updates.on_postponed = lambda: show_notification(
            "JPEGdrop", "Das Update wartet auf die aktiven Aufträge. Neue Drops sind bis zum Neustart gesperrt.",
            subtitle="Update wartet")
        return self

    def applicationDidFinishLaunching_(self, notification):
        mb = Foundation.NSBundle.mainBundle()
        _dbg("didFinishLaunching: mainBundle=%s id=%s" % (mb.bundlePath(), mb.bundleIdentifier()))
        # setActivationPolicy_ wird bereits vor NSApp.run() in main() gesetzt;
        # hier ist es redundant und wird weggelassen.
        self._build_main_menu()
        self._build_status_item()
        _dbg("status item gebaut")
        self._settings_controller = self._make_settings_controller()
        self._jobs_timer = Foundation.NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            0.15, self, "pollJobs:", None, True)
        runloop = Foundation.NSRunLoop.mainRunLoop()
        runloop.addTimer_forMode_(self._jobs_timer, Foundation.NSRunLoopCommonModes)
        # NSTerminateLater wartet im modalen Runloop. Ohne diesen Modus würde
        # der fertige Worker nie die abschließende Beenden-Antwort auslösen.
        runloop.addTimer_forMode_(self._jobs_timer, AppKit.NSModalPanelRunLoopMode)
        signal.signal(signal.SIGTERM, self._stop_signal)
        signal.signal(signal.SIGINT, self._stop_signal)
        self._updates.configure(self._jobs)
        _dbg("didFinishLaunching fertig")

    def _make_settings_controller(self):
        controller = SettingsWindowController.alloc().init()
        controller._on_saved = self._settings_saved
        controller._update_controller = self._updates
        return controller

    def _settings_saved(self):
        """Nach Speichern im ⌘,-Fenster Statuszeile und Menü-Häkchen nachziehen."""
        self._refresh_codec_submenu()
        self._refresh_preset_submenu()
        self._status_menu_item.setTitle_(self._current_status_text())

    def _build_main_menu(self):
        """Echtes Hauptmenü (Menüleiste, wenn die App aktiv ist).

        Nötig, damit ⌘, und ⌘Q app-weit funktionieren: Key-Equivalents im
        JD-Status-Menü gelten nur, solange genau dieses Menü offen ist —
        ohne Hauptmenü lief ⌘, deshalb ins Leere."""
        main_menu = NSMenu.alloc().init()
        app_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("JPEGdrop", None, "")
        app_menu = NSMenu.alloc().initWithTitle_("JPEGdrop")

        prefs = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Einstellungen…", None, ",")
        prefs.setTarget_(self)
        prefs.setAction_(objc.selector(self.showSettings_))
        app_menu.addItem_(prefs)
        jobs = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Aufträge…", "showJobs:", "0")
        jobs.setTarget_(self)
        app_menu.addItem_(jobs)
        update = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Nach Updates suchen…", "checkForUpdates:", "")
        update.setTarget_(self._updates)
        app_menu.addItem_(update)
        self._add_information_items(app_menu)

        app_menu.addItem_(NSMenuItem.separatorItem())
        # Target nil -> geht an den First Responder (Key-Window / NSApp).
        close_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Fenster schließen", "performClose:", "w"
        )
        app_menu.addItem_(close_item)
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "JPEGdrop beenden", "terminate:", "q"
        )
        app_menu.addItem_(quit_item)

        app_item.setSubmenu_(app_menu)
        main_menu.addItem_(app_item)
        NSApp.setMainMenu_(main_menu)
        # Der direkte Entwicklerstart kann noch den Python-Prozessnamen zeigen.
        app_menu.setTitle_("JPEGdrop")

    @objc.python_method
    def _add_information_items(self, menu):
        menu.addItem_(NSMenuItem.separatorItem())
        for title, action in (("Projekt und Hilfe…", "showProject:"),
                              ("Datenschutz…", "showPrivacy:"), ("Impressum…", "showLegal:")):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
            item.setTarget_(self)
            menu.addItem_(item)

    def showProject_(self, sender):
        AppKit.NSWorkspace.sharedWorkspace().openURL_(Foundation.NSURL.URLWithString_(
            "https://github.com/danielmuellerir/jpegdrop"))

    def showPrivacy_(self, sender):
        local = Path(__file__).resolve().parents[1] / "privacy.html"
        if not local.is_file():
            local = Path(__file__).resolve().parents[1] / "assets/privacy.html"
        AppKit.NSWorkspace.sharedWorkspace().openURL_(Foundation.NSURL.fileURLWithPath_(str(local)))

    def showLegal_(self, sender):
        AppKit.NSWorkspace.sharedWorkspace().openURL_(Foundation.NSURL.URLWithString_("https://dm0.de/impressum.html"))

    def _build_status_item(self):
        self._status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(-1.0)  # variable
        btn = self._status_item.button()
        # Titel: "JD" als gut sichtbares Kürzel (kein App-Icon-Bundle nötig)
        btn.setTitle_("JD")

        menu = NSMenu.alloc().init()

        # Helper: NSMenuItem mit Action. PyObjC 10 braucht einen Selector.
        # Wir bauen ihn aus der gebundenen Methode. Methodennamen mit
        # trailing Underscore (z.B. setCodec_) werden automatisch zu
        # Cocoa-Selektoren (z.B. "setCodec:") — wir versuchen es zuerst
        # mit dem Namen wie gegeben, dann mit '_'-Suffix.
        def menu_item(title, method_name=None, key=""):
            it = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                title, None, key
            )
            if method_name is not None:
                bound = (
                    getattr(self, method_name, None)
                    or getattr(self, method_name + "_", None)
                )
                if bound is None:
                    raise AttributeError(
                        f"AppDelegate hat keine Methode '{method_name}' "
                        f"(oder '{method_name}_')"
                    )
                it.setTarget_(self)
                it.setAction_(objc.selector(bound))
            return it

        # Quick-Codec-Auswahl
        codec_item = menu_item("Codec")
        codec_submenu = NSMenu.alloc().init()
        for codec in jpegdrop.CODECS.values():
            it = menu_item(codec.label, "setCodec")
            it.setRepresentedObject_(codec.name)
            codec_submenu.addItem_(it)
        codec_item.setSubmenu_(codec_submenu)
        menu.addItem_(codec_item)
        self._codec_submenu = codec_submenu
        self._refresh_codec_submenu()

        # Quick-Preset-Auswahl (dynamisch nach gewähltem Codec)
        preset_item = menu_item("Preset")
        preset_submenu = NSMenu.alloc().init()  # wird in _refresh_preset_submenu gefüllt
        preset_item.setSubmenu_(preset_submenu)
        menu.addItem_(preset_item)
        self._preset_item = preset_item
        self._refresh_preset_submenu()

        menu.addItem_(NSMenuItem.separatorItem())

        # Einstellungen… (⌘,)
        prefs_item = menu_item("Einstellungen…", "showSettings", ",")
        menu.addItem_(prefs_item)
        menu.addItem_(menu_item("Aufträge…", "showJobs", "0"))
        update = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Nach Updates suchen…", "checkForUpdates:", "")
        update.setTarget_(self._updates)
        menu.addItem_(update)

        # Aktueller Status
        status_item = menu_item(self._current_status_text())
        status_item.setEnabled_(False)
        menu.addItem_(status_item)
        self._status_menu_item = status_item

        menu.addItem_(NSMenuItem.separatorItem())

        # Über / Beenden
        about_item = menu_item(f"Über JPEGdrop (v{jpegdrop.__version__})")
        about_item.setEnabled_(False)
        menu.addItem_(about_item)
        self._add_information_items(menu)
        quit_item = menu_item("Beenden", "quit", "q")
        menu.addItem_(quit_item)

        self._status_item.setMenu_(menu)

    def _current_status_text(self) -> str:
        s = jpegdrop.load_settings()
        codec = jpegdrop.CODECS.get(s["codec"])
        codec_label = codec.label if codec else s["codec"]
        return f"Aktiv: {codec_label} · {s['preset']}"

    def _refresh_preset_submenu(self):
        s = jpegdrop.load_settings()
        codec = jpegdrop.CODECS.get(s["codec"])
        if not codec:
            return
        submenu = NSMenu.alloc().init()
        # Selector aus der gebundenen Methode (Trailing-Underscore-Form).
        set_preset_sel = objc.selector(self.setPreset_)
        for p in codec.presets:
            it = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                p.label, None, ""
            )
            it.setRepresentedObject_(p.name)
            it.setToolTip_(p.description)
            it.setTarget_(self)
            it.setAction_(set_preset_sel)
            # Häkchen am aktuellen Preset
            if p.name == s["preset"]:
                it.setState_(1)  # NSOnState
            submenu.addItem_(it)
        self._preset_item.setSubmenu_(submenu)

    def _refresh_codec_submenu(self):
        active = jpegdrop.load_settings()["codec"]
        for item in self._codec_submenu.itemArray():
            item.setState_(1 if item.representedObject() == active else 0)

    # Selectors ------------------------------------------------------------
    def setCodec_(self, sender):
        codec_name = sender.representedObject()
        if not codec_name:
            return
        s = jpegdrop.load_settings()
        # Preset auf gültigen Wert im neuen Codec mappen (sonst Default)
        codec = jpegdrop.CODECS[codec_name]
        valid = [p.name for p in codec.presets]
        if s["preset"] not in valid:
            s["preset"] = codec.default_preset
        s["codec"] = codec_name
        jpegdrop.save_settings(s)
        self._refresh_codec_submenu()
        self._refresh_preset_submenu()
        self._status_menu_item.setTitle_(self._current_status_text())

    def setPreset_(self, sender):
        preset_name = sender.representedObject()
        if not preset_name:
            return
        s = jpegdrop.load_settings()
        s["preset"] = preset_name
        jpegdrop.save_settings(s)
        self._refresh_preset_submenu()
        self._status_menu_item.setTitle_(self._current_status_text())

    def showSettings_(self, _sender):
        if self._settings_controller is None:
            self._settings_controller = self._make_settings_controller()
        self._settings_controller.show()

    def showJobs_(self, _sender):
        if self._jobs_controller is None:
            self._jobs_controller = JobsWindowController.alloc().init()
            self._jobs_controller.manager = self._jobs
        self._jobs_controller.show()

    @property
    def has_active_jobs(self):
        return self._jobs.has_active_jobs

    @objc.python_method
    def _stop_signal(self, _signum, _frame):
        # Ein Installer stoppt die App per SIGTERM. Auch dieser Weg muss auf
        # eigene Encoder warten; der Timer führt AppKit-Aufrufe regulär aus.
        self._external_stop = True

    def pollJobs_(self, _timer):
        if self._external_stop and not self._terminating:
            if not self._stop_scheduled:
                self._stop_scheduled = True
                self._jobs.shutdown(wait=False)
                # terminate: kann selbst einen modalen Runloop starten. Der
                # Jobs-Timer muss dafür zuerst seinen Callback verlassen.
                NSApp.performSelector_withObject_afterDelay_("terminate:", None, 0.0)
            return
        if not self._terminating:
            self._updates.poll()
        revision, jobs = self._jobs.snapshot(after_revision=self._jobs_revision)
        if revision != self._jobs_revision:
            self._jobs_revision = revision
            if self._jobs_controller is not None:
                self._jobs_controller.refresh(jobs)
            running = next((j for j in jobs if j.state in {"running", "cancelling"}), None)
            active = sum(j.active for j in jobs)
            self._status_item.button().setTitle_(f"JD {running.completed}/{len(running.paths)}" if running else "JD")
            self._status_item.button().setToolTip_(
                f"{active} aktive Aufträge" + (f" · {running.current}" if running and running.current else ""))
            for job in jobs:
                if not job.active and job.id not in self._notified_jobs:
                    self._notified_jobs.add(job.id)
                    message = jpegdrop.batch_message(job.all_results)
                    if job.problem:
                        message += f"\n{job.problem}"
                    show_notification(f"JPEGdrop · Auftrag {job.number}", message,
                                      success=job.state == "completed",
                                      subtitle={"cancelled": "abgebrochen", "partial": "Teilerfolg",
                                                "failed": "Fehler", "completed": "fertig"}[job.state])
            self._notified_jobs.intersection_update(j.id for j in jobs)
        if self._terminating and self._jobs.stopped:
            self._jobs_timer.invalidate()
            NSApp.replyToApplicationShouldTerminate_(True)

    def applicationShouldTerminate_(self, sender):
        if self._terminating:
            return AppKit.NSTerminateLater
        if self.has_active_jobs and not self._external_stop:
            alert = AppKit.NSAlert.alloc().init()
            icon = AppKit.NSImage.alloc().initWithContentsOfFile_(str(HERE.parent / "applet.icns"))
            if icon is not None:
                # mainBundle ist beim eingebetteten Interpreter Python.app.
                alert.setIcon_(icon)
            alert.setMessageText_("Aktive Aufträge abbrechen und JPEGdrop beenden?")
            alert.setInformativeText_(
                "Laufende und wartende Aufträge werden abgebrochen. Bereits erzeugte Dateien bleiben erhalten.")
            alert.addButtonWithTitle_("Weiterarbeiten")
            alert.addButtonWithTitle_("Abbrechen und beenden")
            self._quit_dialog_open = True
            try:
                choice = alert.runModal()
            finally:
                self._quit_dialog_open = False
            if choice != AppKit.NSAlertSecondButtonReturn:
                return AppKit.NSTerminateCancel
        if self.has_active_jobs:
            self._terminating = True
            self._jobs.shutdown(wait=False)
            return AppKit.NSTerminateLater
        self._jobs.shutdown()
        return AppKit.NSTerminateNow

    def quit_(self, _sender):
        NSApp.terminate_(None)

    # Dock-Rechtsklick-Menü -------------------------------------------------
    def applicationDockMenu_(self, sender):
        """Rechtsklick aufs Dock-Icon: Codec/Preset/Einstellungen. Wird bei jedem
        Öffnen frisch gebaut und spiegelt die aktuellen Settings (Häkchen)."""
        s = jpegdrop.load_settings()
        menu = NSMenu.alloc().init()

        codec_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Codec", None, "")
        codec_sub = NSMenu.alloc().init()
        for codec in jpegdrop.CODECS.values():
            it = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(codec.label, None, "")
            it.setRepresentedObject_(codec.name)
            it.setTarget_(self)
            it.setAction_(objc.selector(self.setCodec_))
            if codec.name == s["codec"]:
                it.setState_(1)
            codec_sub.addItem_(it)
        codec_item.setSubmenu_(codec_sub)
        menu.addItem_(codec_item)

        preset_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Preset", None, "")
        preset_sub = NSMenu.alloc().init()
        codec = jpegdrop.CODECS.get(s["codec"])
        if codec:
            for p in codec.presets:
                it = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(p.label, None, "")
                it.setRepresentedObject_(p.name)
                it.setTarget_(self)
                it.setAction_(objc.selector(self.setPreset_))
                if p.name == s["preset"]:
                    it.setState_(1)
                preset_sub.addItem_(it)
        preset_item.setSubmenu_(preset_sub)
        menu.addItem_(preset_item)

        menu.addItem_(NSMenuItem.separatorItem())
        prefs = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Einstellungen…", None, ",")
        prefs.setTarget_(self)
        prefs.setAction_(objc.selector(self.showSettings_))
        menu.addItem_(prefs)
        jobs = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Aufträge…", "showJobs:", "")
        jobs.setTarget_(self)
        menu.addItem_(jobs)
        return menu

    # Drop-Handling --------------------------------------------------------
    def application_openFiles_(self, sender, filenames):  # Finder -> drop auf App-Icon (alt)
        # Mappt auf Cocoa-Selector 'application:openFiles:' (2 Args).
        _dbg("application:openFiles: %r" % (list(filenames),))
        if isinstance(filenames, (list, tuple)):
            paths = [str(p) for p in filenames]
        else:
            paths = [str(filenames)]
        self._handle_paths(paths)
        NSApp.replyToOpenOrPrint_(0)  # NSApplicationDelegateReplySuccess

    def application_openURLs_(self, app, urls):  # moderne Drop-API (macOS 10.13+)
        # `application:openFiles:` ist deprecated und wird auf aktuellen macOS-
        # Versionen oft NICHT mehr aufgerufen -> die moderne URL-Variante zusätzlich.
        _dbg("application:openURLs: %r" % ([str(u.path()) for u in urls],))
        paths = []
        for u in urls:
            try:
                p = u.path() if u.isFileURL() else None
            except Exception:
                p = None
            if p:
                paths.append(str(p))
        # Nur der moderne Pfad meldet Fehl-Drops zurück — der deprecated
        # openFiles-Pfad bleibt still (Start-Falle, s. _handle_paths).
        self._handle_paths(paths, notify_if_empty=True)

    def _handle_paths(self, paths, notify_if_empty=False):
        # Bilddateien und Ordner an den Worker geben. Wichtig: die deprecated
        # `application:openFiles:` feuert beim Start einmal mit dem Skriptpfad
        # (menu_app.py) als vermeintlichem „Dokument" — ohne diesen Filter würde
        # die App beim Start versuchen, ihr eigenes .py zu konvertieren (Fehler).
        real = [p for p in paths
                if os.path.isdir(p) or (os.path.isfile(p) and Path(p).suffix.lower() in IMAGE_EXTS)]
        if not real:
            # Feedback statt stummem Verschlucken, wenn jemand z.B. ein PDF
            # oder einen Ordner droppt. Doppelter Schutz gegen die Start-
            # Falle: nur auf dem openURLs-Pfad (notify_if_empty) und nie,
            # wenn nur .py-Pfade ankamen (das eigene Skript als Pseudo-Drop).
            if notify_if_empty and any(Path(p).suffix.lower() != ".py" for p in paths):
                show_notification(
                    "JPEGdrop",
                    "Keine unterstützten Bilddateien im Drop.",
                    success=False,
                )
            return
        try:
            skipped = {p: "Datei fehlt oder das Format wird vom Droplet nicht unterstützt."
                       for p in paths if p not in real}
            self._jobs.submit(real, jpegdrop.load_settings(), skipped=skipped)
        except ValueError as exc:
            show_notification("JPEGdrop", str(exc), success=False)



# ===========================================================================
# main
# ===========================================================================
def _fix_bundle_name():
    """Bei direktem Start aus den Quellen kann mainBundle Python.app sein.
    Der native Bundle-Starter liefert bereits die JPEGdrop-Identität.
    das Info-Dictionary des laufenden Prozesses ist zur Laufzeit mutabel;
    CFBundleName dort überschreiben, BEVOR NSApplication erstmals angefasst
    wird. Rein kosmetisch, darum fail-open."""
    try:
        info = Foundation.NSBundle.mainBundle().infoDictionary()
        info["CFBundleName"] = "JPEGdrop"
        info["CFBundleDisplayName"] = "JPEGdrop"
    except Exception:
        pass


def main():
    global NSApp
    _dbg("main() start (pid %d, argv=%r)" % (os.getpid(), sys.argv))
    _fix_bundle_name()
    # Delegate registrieren. WICHTIG: starke Referenz halten — NSApplication
    # haelt das Delegate nur schwach; ohne globale Referenz koennte es weg-GC't
    # werden und Drop-/Menü-Callbacks blieben aus.
    global _DELEGATE
    _DELEGATE = AppDelegate.alloc().init()
    NSApp = NSApplication.sharedApplication()
    NSApp.setDelegate_(_DELEGATE)
    # Reguläre App MIT Dock-Icon (Dock-Droplet).
    NSApp.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    NSApp.activateIgnoringOtherApps_(True)
    NSApp.run()


if __name__ == "__main__":
    main()
