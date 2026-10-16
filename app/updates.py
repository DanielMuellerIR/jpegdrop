"""Sparkle mit explizitem äußeren App-Bundle und Schutz aktiver Aufträge."""
from pathlib import Path

import objc
import Foundation as F


objc.registerMetaDataForSelector(b"SPUUpdater", b"startUpdater:", {
    "arguments": {2: {"type_modifier": b"o"}},
})
objc.registerMetaDataForSelector(
    b"JDUpdateController", b"updater:shouldPostponeRelaunchForUpdate:untilInvokingBlock:", {
        "arguments": {4: {"callable": {"retval": {"type": b"v"}, "arguments": {0: {"type": b"^v"}}}}},
    },
)


class JDUpdateController(F.NSObject):
    def init(self):
        self = objc.super(JDUpdateController, self).init()
        if self is None:
            return None
        self.available = False
        self.problem = "Updates benötigen die gebaute JPEGdrop-App."
        self.last_error = None
        self.updater = None
        self.driver = None
        self.bundle = None
        self.jobs = None
        self.deferred_install = None
        self.installing = False
        self.can_relaunch = lambda: True
        self.on_postponed = None
        return self

    @objc.python_method
    def configure(self, jobs, bundle_path=None):
        self.jobs = jobs
        root = Path(bundle_path) if bundle_path else Path(__file__).resolve().parents[3]
        if root.suffix != ".app" or not (root / "Contents/Info.plist").is_file():
            return False
        try:
            bundle = F.NSBundle.bundleWithPath_(str(root))
            if bundle.bundleIdentifier() != "de.dm0.jpegdrop":
                raise ValueError("Das Update-Ziel ist keine JPEGdrop-App.")
            framework = root / "Contents/Frameworks/Sparkle.framework"
            objc.loadBundle("JDSparkle", {}, bundle_path=str(framework))
            driver_class = objc.lookUpClass("SPUStandardUserDriver")
            updater_class = objc.lookUpClass("SPUUpdater")
            self.bundle = bundle
            self.driver = driver_class.alloc().initWithHostBundle_delegate_(bundle, None)
            self.updater = updater_class.alloc().initWithHostBundle_applicationBundle_userDriver_delegate_(
                bundle, bundle, self.driver, self)
            started, error = self.updater.startUpdater_(None)
            if not started:
                raise ValueError(str(error.localizedDescription()) if error else "Sparkle konnte nicht starten.")
            # Diese Optionen sind in JPEGdrop nicht freigegeben, auch wenn alte
            # Host-Defaults von einem früheren Test noch abweichende Werte haben.
            if self.updater.sendsSystemProfile():
                self.updater.setSendsSystemProfile_(False)
            if self.updater.automaticallyDownloadsUpdates():
                self.updater.setAutomaticallyDownloadsUpdates_(False)
            self.available = True
            self.problem = None
            return True
        except (ImportError, OSError, ValueError, objc.error) as error:
            self.problem = f"Updates sind derzeit nicht verfügbar: {error}"
            return False

    @property
    def automatic_checks(self):
        return bool(self.available and self.updater.automaticallyChecksForUpdates())

    @objc.python_method
    def set_automatic_checks(self, enabled):
        if self.available:
            self.updater.setAutomaticallyChecksForUpdates_(bool(enabled))

    def checkForUpdates_(self, sender):
        if self.available and self.updater.canCheckForUpdates():
            self.updater.checkForUpdates()

    @objc.typedSelector(b"B@:@")
    def validateMenuItem_(self, item):
        return bool(self.available and not self.installing and self.deferred_install is None
                    and self.updater.canCheckForUpdates())

    @objc.typedSelector(b"B@:@@@?")
    def updater_shouldPostponeRelaunchForUpdate_untilInvokingBlock_(self, updater, item, handler):
        ready = self.jobs.prepare_for_update()
        if ready and self.can_relaunch():
            self.installing = True
            return False
        self.deferred_install = handler
        if self.on_postponed:
            self.on_postponed()
        return True

    @objc.python_method
    def poll(self):
        if self.deferred_install is not None and not self.jobs.has_active_jobs and self.can_relaunch():
            handler, self.deferred_install = self.deferred_install, None
            self.installing = True
            handler()

    @objc.typedSelector(b"v@:@q@")
    def updater_didFinishUpdateCycleForUpdateCheck_error_(self, updater, update_check, error):
        if error is not None:
            self.last_error = error
            self.installing = False
            self.deferred_install = None
            self.jobs.resume_after_update()
