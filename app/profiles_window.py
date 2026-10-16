"""Native Profilverwaltung; Bibliothek und ungespeichertes Formular bleiben getrennt."""
from __future__ import annotations

import objc
import AppKit as A
from Foundation import NSObject

from app.profiles import ProfileStore, OPTION_KEYS, apply_profile, profile_summary


class ProfilesWindowController(NSObject):
    def init(self):
        self = objc.super(ProfilesWindowController, self).init()
        if self is None:
            return None
        self.owner = None
        self._window = None
        self._profiles = ()
        self._buttons = []
        return self

    def show(self):
        if self._window is None:
            self._build()
        self.reload_(None)
        self._window.makeKeyAndOrderFront_(None)

    def hide(self):
        if self._window is not None:
            self._window.orderOut_(None)

    def _build(self):
        window = A.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            A.NSMakeRect(0, 0, 560, 400), A.NSWindowStyleMaskTitled | A.NSWindowStyleMaskClosable,
            A.NSBackingStoreBuffered, False)
        window.setTitle_("JPEGdrop Profile")
        window.setReleasedWhenClosed_(False)
        table = A.NSTableView.alloc().initWithFrame_(A.NSMakeRect(0, 0, 520, 170))
        column = A.NSTableColumn.alloc().initWithIdentifier_("name")
        column.setTitle_("Gespeicherte Profile"); column.setWidth_(515)
        column.setEditable_(False)
        column.dataCell().setLineBreakMode_(A.NSLineBreakByTruncatingTail)
        table.addTableColumn_(column)
        table.setColumnAutoresizingStyle_(A.NSTableViewLastColumnOnlyAutoresizingStyle)
        table.setRowHeight_(28); table.setAllowsMultipleSelection_(False)
        table.setDataSource_(self); table.setDelegate_(self)
        table.setAccessibilityLabel_("Gespeicherte Profile")
        scroll = A.NSScrollView.alloc().initWithFrame_(A.NSMakeRect(0, 0, 520, 180))
        scroll.setHasVerticalScroller_(True); scroll.setBorderType_(A.NSBezelBorder)
        scroll.setDocumentView_(table)
        scroll.heightAnchor().constraintEqualToConstant_(180).setActive_(True)
        detail = A.NSTextField.wrappingLabelWithString_("Bitte ein Profil auswählen.")
        detail.setPreferredMaxLayoutWidth_(520)
        detail.heightAnchor().constraintEqualToConstant_(80).setActive_(True)
        detail.setMaximumNumberOfLines_(4)
        detail.setLineBreakMode_(A.NSLineBreakByTruncatingTail)
        detail.setTextColor_(A.NSColor.secondaryLabelColor())
        name = A.NSTextField.alloc().initWithFrame_(A.NSMakeRect(0, 0, 380, 24))
        name.setAccessibilityLabel_("Profilname")
        name.widthAnchor().constraintEqualToConstant_(390).setActive_(True)
        name_row = A.NSStackView.stackViewWithViews_([A.NSTextField.labelWithString_("Name:"), name])
        name_row.setSpacing_(10)
        rows = []
        for choices in ((("Neu aus Formular", "create:"), ("Umbenennen", "rename:"),
                          ("Aus Formular aktualisieren", "update:")),
                         (("Im Formular anwenden", "apply:"), ("Löschen…", "delete:"),
                          ("Neu laden", "reload:"), ("Schließen", "close:"))):
            buttons = [A.NSButton.buttonWithTitle_target_action_(title, self, action) for title, action in choices]
            self._buttons.extend(buttons)
            row = A.NSStackView.stackViewWithViews_(buttons); row.setSpacing_(10); rows.append(row)
        info = A.NSTextField.wrappingLabelWithString_(
            "Neu und Aktualisieren übernehmen die Bild- und Ablagewerte aus dem offenen Formular. "
            "Profiländerungen werden sofort gespeichert. Anwenden ändert nur das Formular; "
            "erst dessen Speichern macht die Auswahl für neue Drops aktiv.")
        info.setFont_(A.NSFont.systemFontOfSize_(A.NSFont.smallSystemFontSize()))
        info.setTextColor_(A.NSColor.secondaryLabelColor()); info.setPreferredMaxLayoutWidth_(520)
        error = A.NSTextField.wrappingLabelWithString_("")
        error.setPreferredMaxLayoutWidth_(520); error.setTextColor_(A.NSColor.systemRedColor())
        error.setHidden_(True)
        content = A.NSStackView.stackViewWithViews_([scroll, detail, name_row, *rows, info, error])
        content.setOrientation_(A.NSUserInterfaceLayoutOrientationVertical)
        content.setAlignment_(A.NSLayoutAttributeLeading); content.setSpacing_(16)
        cv = window.contentView(); cv.addSubview_(content)
        content.setTranslatesAutoresizingMaskIntoConstraints_(False)
        for anchor, target, amount in ((content.leadingAnchor(), cv.leadingAnchor(), 20),
                                       (content.trailingAnchor(), cv.trailingAnchor(), -20),
                                       (content.topAnchor(), cv.topAnchor(), 20),
                                       (content.bottomAnchor(), cv.bottomAnchor(), -20)):
            anchor.constraintEqualToAnchor_constant_(target, amount).setActive_(True)
        for view in (scroll, detail, info, error):
            view.widthAnchor().constraintEqualToAnchor_(content.widthAnchor()).setActive_(True)
        self._window, self._table, self._name, self._detail, self._error = window, table, name, detail, error
        self._resize(); window.center()

    def _resize(self):
        self._window.setContentSize_(self._window.contentView().fittingSize())

    def numberOfRowsInTableView_(self, table):
        return len(self._profiles)

    def tableView_objectValueForTableColumn_row_(self, table, column, row):
        return self._profiles[row].name if 0 <= row < len(self._profiles) else ""

    def tableViewSelectionDidChange_(self, notification):
        selected = self._selected()
        self._name.setStringValue_(selected.name if selected else "")
        self._detail.setStringValue_(profile_summary(selected) if selected else "Bitte ein Profil auswählen.")
        self._detail.setToolTip_(profile_summary(selected) if selected else "")
        for index in (1, 2, 3, 4):
            self._buttons[index].setEnabled_(selected is not None)
        self._resize()

    def _selected(self):
        row = self._table.selectedRow()
        return self._profiles[row] if 0 <= row < len(self._profiles) else None

    @objc.python_method
    def _reload(self, identifier=None):
        self._profiles = ProfileStore().list()
        self._table.reloadData()
        row = next((i for i, p in enumerate(self._profiles) if p.id == identifier), -1)
        if row >= 0:
            from Foundation import NSIndexSet
            self._table.selectRowIndexes_byExtendingSelection_(NSIndexSet.indexSetWithIndex_(row), False)
        else:
            self._table.deselectAll_(None)
        self.tableViewSelectionDidChange_(None)
        self.owner._refresh_profile_selection()
        if self.owner._on_saved is not None:
            self.owner._on_saved()

    @objc.python_method
    def _perform(self, operation):
        try:
            result = operation()
            self._error.setHidden_(True)
            self._reload(result.id if result else None)
        except (ValueError, OSError) as error:
            self._error.setStringValue_(str(error)); self._error.setHidden_(False)
        self._resize()

    def reload_(self, sender):
        self._perform(lambda: self._selected())

    def create_(self, sender):
        def create():
            values = self.owner._form_settings()
            return ProfileStore().create(self._name.stringValue(), {key: values[key] for key in OPTION_KEYS})
        self._perform(create)

    def rename_(self, sender):
        selected = self._selected()
        if selected:
            self._perform(lambda: ProfileStore().update(selected.id, name=self._name.stringValue(), expected=selected))

    @objc.python_method
    def _confirm(self, title, detail, button):
        alert = A.NSAlert.alloc().init()
        alert.setMessageText_(title); alert.setInformativeText_(detail)
        alert.addButtonWithTitle_("Abbrechen"); alert.addButtonWithTitle_(button)
        return alert.runModal() == A.NSAlertSecondButtonReturn

    def update_(self, sender):
        selected = self._selected()
        if selected and self._confirm(f'Profil „{selected.name}“ aktualisieren?',
                "Die gespeicherten Bild- und Ablageoptionen werden durch die aktuellen Formularwerte ersetzt.", "Aktualisieren"):
            def update():
                values = self.owner._form_settings()
                return ProfileStore().update(selected.id, options={key: values[key] for key in OPTION_KEYS}, expected=selected)
            self._perform(update)

    def delete_(self, sender):
        selected = self._selected()
        if selected and self._confirm(f'Profil „{selected.name}“ löschen?',
                "Bilder, aktuelle Bildoptionen und bereits eingereihte Aufträge bleiben erhalten.", "Löschen"):
            self._perform(lambda: ProfileStore().delete(selected.id, expected=selected))

    def apply_(self, sender):
        selected = self._selected()
        if selected:
            def apply():
                current = next((p for p in ProfileStore().list() if p.id == selected.id), None)
                if current != selected:
                    raise ValueError("Das Profil wurde inzwischen geändert; bitte neu laden.")
                self.owner._set_image_settings(apply_profile({}, current))
                return current
            self._perform(apply)

    def close_(self, sender):
        self.hide()
