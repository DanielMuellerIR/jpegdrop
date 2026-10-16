"""Native Auftrags- und Ergebnisansicht; alle Aufrufe erfolgen auf dem Hauptthread."""
from pathlib import Path

import objc
import AppKit as A
import Foundation as F

from app.jobs import STATE_LABELS
import jpegdrop


def result_state(result: dict) -> str:
    return {"ok": "Erzeugt", "warning": "Mit Warnung", "error": "Fehler",
            "cancelled": "Abgebrochen", "skipped": "Übersprungen"}.get(result.get("status"), "Unbekannt")


def result_detail(result: dict) -> str:
    if result.get("status") == "skipped":
        return result["skip_reason"]
    if result.get("status") == "cancelled":
        return "Nicht fertig verarbeitet; keine neue Ausgabe."
    if result.get("error"):
        return result["error"]
    warnings = " ".join(w["message"] for w in result.get("warnings", []))
    detail = warnings or (f"{jpegdrop.human_size(result['out_bytes'])} · {Path(result['target']).name}"
                          if result.get("target") else "")
    if result.get("output_width"):
        dimensions = f"{result['output_width']} × {result['output_height']} px"
        if result.get("resized"):
            dimensions = f"{result['source_width']} × {result['source_height']} → {dimensions}"
        detail = f"{dimensions} · {detail}"
        if result.get("alpha_preserved"):
            detail += " · Transparenz erhalten"
    return detail


class JobsWindowController(F.NSObject):
    def init(self):
        self = objc.super(JobsWindowController, self).init()
        if self is None:
            return None
        self.manager = None
        self._window = None
        self._jobs = []
        self._job_table = None
        self._result_table = None
        self._selected_id = None
        self._updating = False
        return self

    @objc.python_method
    def show(self):
        if self._window is None:
            self._build()
        self.refresh(self.manager.snapshot()[1])
        self._window.makeKeyAndOrderFront_(None)
        A.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    @objc.python_method
    def _table(self, columns):
        table = A.NSTableView.alloc().initWithFrame_(A.NSMakeRect(0, 0, 740, 150))
        for identifier, title, width in columns:
            column = A.NSTableColumn.alloc().initWithIdentifier_(identifier)
            column.headerCell().setStringValue_(title)
            column.setWidth_(width)
            column.setMinWidth_(80)
            column.dataCell().setFont_(A.NSFont.systemFontOfSize_(13))
            column.dataCell().setLineBreakMode_(A.NSLineBreakByTruncatingTail)
            table.addTableColumn_(column)
        table.setRowHeight_(25)
        table.setUsesAlternatingRowBackgroundColors_(True)
        table.setColumnAutoresizingStyle_(A.NSTableViewLastColumnOnlyAutoresizingStyle)
        table.setDataSource_(self)
        table.setDelegate_(self)
        table.setAllowsEmptySelection_(False)
        scroll = A.NSScrollView.alloc().init()
        scroll.setDocumentView_(table)
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setBorderType_(A.NSBezelBorder)
        return table, scroll

    @objc.python_method
    def _build(self):
        win = A.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            A.NSMakeRect(0, 0, 800, 640),
            A.NSWindowStyleMaskTitled | A.NSWindowStyleMaskClosable | A.NSWindowStyleMaskResizable,
            A.NSBackingStoreBuffered, False)
        win.setTitle_("JPEGdrop Aufträge")
        win.setReleasedWhenClosed_(False)
        win.setContentMinSize_(A.NSMakeSize(720, 560))
        stack = A.NSStackView.alloc().init()
        stack.setOrientation_(A.NSUserInterfaceLayoutOrientationVertical)
        stack.setAlignment_(A.NSLayoutAttributeLeading)
        stack.setSpacing_(12)
        self._summary = A.NSTextField.labelWithString_("Noch keine Aufträge. Bilder auf das Dock-Icon ziehen.")
        stack.addArrangedSubview_(self._summary)
        self._job_table, jobs_scroll = self._table([
            ("title", "Auftrag", 260), ("format", "Format", 125),
            ("state", "Status", 180), ("progress", "Bilder", 110)])
        jobs_scroll.heightAnchor().constraintEqualToConstant_(150).setActive_(True)
        stack.addArrangedSubview_(jobs_scroll)
        self._progress = A.NSTextField.labelWithString_("Keine Auswahl")
        self._progress.setLineBreakMode_(A.NSLineBreakByTruncatingTail)
        stack.addArrangedSubview_(self._progress)
        self._result_table, results_scroll = self._table([
            ("source", "Quelldatei", 240), ("state", "Ergebnis", 120),
            ("detail", "Ausgabe / Grund", 380)])
        results_scroll.heightAnchor().constraintGreaterThanOrEqualToConstant_(140).setActive_(True)
        stack.addArrangedSubview_(results_scroll)
        detail_scroll = A.NSScrollView.alloc().init()
        detail_scroll.setHasVerticalScroller_(True)
        detail_scroll.setAutohidesScrollers_(True)
        detail_scroll.setBorderType_(A.NSBezelBorder)
        self._detail = A.NSTextView.alloc().initWithFrame_(A.NSMakeRect(0, 0, 750, 100))
        self._detail.setEditable_(False)
        self._detail.setFont_(A.NSFont.systemFontOfSize_(12))
        self._detail.setTextContainerInset_(A.NSMakeSize(8, 6))
        self._detail.setHorizontallyResizable_(False)
        self._detail.setAutoresizingMask_(A.NSViewWidthSizable)
        self._detail.textContainer().setWidthTracksTextView_(True)
        detail_scroll.setDocumentView_(self._detail)
        detail_scroll.heightAnchor().constraintEqualToConstant_(100).setActive_(True)
        stack.addArrangedSubview_(detail_scroll)
        buttons = A.NSStackView.alloc().init()
        buttons.setOrientation_(A.NSUserInterfaceLayoutOrientationHorizontal)
        buttons.setSpacing_(12)
        self._cancel = A.NSButton.buttonWithTitle_target_action_("Auftrag abbrechen", self, "cancel:")
        self._reveal = A.NSButton.buttonWithTitle_target_action_("Erzeugte Dateien im Finder", self, "reveal:")
        buttons.addArrangedSubview_(self._cancel)
        buttons.addArrangedSubview_(self._reveal)
        stack.addArrangedSubview_(buttons)
        cv = win.contentView()
        cv.addSubview_(stack)
        stack.setTranslatesAutoresizingMaskIntoConstraints_(False)
        for anchor, other, offset in [
            (stack.leadingAnchor(), cv.leadingAnchor(), 20),
            (stack.trailingAnchor(), cv.trailingAnchor(), -20),
            (stack.topAnchor(), cv.topAnchor(), 20),
            (stack.bottomAnchor(), cv.bottomAnchor(), -20),
        ]:
            anchor.constraintEqualToAnchor_constant_(other, offset).setActive_(True)
        for view in (self._summary, jobs_scroll, self._progress, results_scroll, detail_scroll):
            view.widthAnchor().constraintEqualToAnchor_(stack.widthAnchor()).setActive_(True)
        win.center()
        self._window = win

    @objc.python_method
    def selected_job(self):
        return next((j for j in self._jobs if j.id == self._selected_id), None)

    @objc.python_method
    def refresh(self, jobs):
        if self._window is None:
            return
        self._updating = True
        self._jobs = jobs
        active = sum(j.active for j in jobs)
        waiting = sum(j.state == "queued" for j in jobs)
        self._summary.setStringValue_(f"{active} aktive Aufträge · {waiting} wartend · {len(jobs)} in dieser Sitzung"
                                      if jobs else "Noch keine Aufträge. Bilder auf das Dock-Icon ziehen.")
        self._job_table.reloadData()
        if not any(j.id == self._selected_id for j in jobs):
            self._selected_id = jobs[-1].id if jobs else None
        if self._selected_id:
            row = next(i for i, j in enumerate(jobs) if j.id == self._selected_id)
            self._job_table.selectRowIndexes_byExtendingSelection_(F.NSIndexSet.indexSetWithIndex_(row), False)
        self._updating = False
        self._refresh_results()

    @objc.python_method
    def _refresh_results(self):
        row = self._result_table.selectedRow()
        job = self.selected_job()
        self._updating = True
        self._result_table.reloadData()
        if job is not None:
            text = ("Ordner und Dateien werden eingelesen …" if job.state == "scanning" else
                    f"{STATE_LABELS[job.state]} · Bilder verarbeitet: {job.completed} / {len(job.paths)}")
            if job.skipped:
                text += f" · {len(job.skipped)} übersprungen"
            if job.current:
                text += f" · {Path(job.current).name}"
            self._progress.setStringValue_(text)
            self._progress.setToolTip_(job.current)
            if job.all_results:
                row = min(max(row, 0), len(job.all_results) - 1)
                self._result_table.selectRowIndexes_byExtendingSelection_(F.NSIndexSet.indexSetWithIndex_(row), False)
        else:
            self._progress.setStringValue_("Keine Auswahl")
        self._cancel.setEnabled_(bool(job and job.active and not job.cancel_requested))
        self._reveal.setEnabled_(bool(job and any(Path(p).is_file() for p in job.targets)))
        self._updating = False
        self._refresh_detail()

    @objc.python_method
    def _refresh_detail(self):
        job = self.selected_job()
        row = self._result_table.selectedRow()
        text = "Ein Ergebnis auswählen, um vollständige Pfade und Meldungen zu sehen."
        if job and 0 <= row < len(job.all_results):
            result = job.all_results[row]
            text = f"Quelle: {result['source']}\n"
            if result.get("target"):
                text += f"Ausgabe: {result['target']}\n"
            text += result_detail(result)
        if job and job.problem:
            text += f"\nAuftrag: {job.problem}"
        self._detail.setString_(text)

    def numberOfRowsInTableView_(self, table):
        if table == self._job_table:
            return len(self._jobs)
        job = self.selected_job()
        return len(job.all_results) if job else 0

    def tableView_objectValueForTableColumn_row_(self, table, column, row):
        key = str(column.identifier())
        if table == self._job_table:
            job = self._jobs[row]
            return {"title": job.title, "format": f"{job.codec.upper()} · {job.preset}",
                    "state": STATE_LABELS[job.state],
                    "progress": "…" if job.state == "scanning" else f"{job.completed} / {len(job.paths)}"}[key]
        result = self.selected_job().all_results[row]
        return {"source": Path(result["source"]).name, "state": result_state(result),
                "detail": result_detail(result)}[key]

    def tableViewSelectionDidChange_(self, notification):
        if self._updating:
            return
        if notification.object() == self._job_table:
            row = self._job_table.selectedRow()
            self._selected_id = self._jobs[row].id if 0 <= row < len(self._jobs) else None
            self._refresh_results()
        else:
            self._refresh_detail()

    def cancel_(self, sender):
        job = self.selected_job()
        if job:
            self.manager.cancel(job.id)
            self.refresh(self.manager.snapshot()[1])

    def reveal_(self, sender):
        job = self.selected_job()
        urls = [F.NSURL.fileURLWithPath_(p) for p in job.targets if Path(p).is_file()] if job else []
        if urls:
            A.NSWorkspace.sharedWorkspace().activateFileViewerSelectingURLs_(urls)
