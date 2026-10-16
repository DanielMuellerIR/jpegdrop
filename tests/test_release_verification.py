"""Release-Prüfungen mit Attrappen: jeder negative Prüfstatus muss durchschlagen."""
from pathlib import Path
import os
import plistlib
import subprocess
import tempfile
import unittest

UTILS = Path(__file__).resolve().parents[1] / "release-utils.sh"


class ReleaseVerificationTests(unittest.TestCase):
    def test_identity_selection_is_unambiguous_and_profile_is_configurable(self):
        first, second = "A" * 40, "B" * 40
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "identities"
            env = {key: value for key, value in os.environ.items()
                   if key not in {"CODESIGN_IDENTITY", "NOTARY_PROFILE"}}
            command = ['/bin/bash', '-c', 'source "$1"; '
                       'security() { cat "$FIXTURE"; }; resolve_signing_identity "$3"',
                       'bash', str(UTILS), str(fixture), str(root)]
            env["FIXTURE"] = str(fixture)
            for text, requested, expected in [
                ("0 valid identities found", None, 1),
                (f'  1) {first} "Developer ID Application: Fixture (TEAM)"', None, 0),
                (f'  1) {first} "Developer ID Application: Fixture (TEAM)"\n'
                 f'  2) {second} "Developer ID Application: Fixture (TEAM)"', None, 2),
                (f'  1) {first} "Developer ID Application: Fixture (TEAM)"\n'
                 f'  2) {second} "Developer ID Application: Fixture (TEAM)"', second, 0),
                (f'  1) {first} "Developer ID Application: Fixture (TEAM)"', "missing", 2),
            ]:
                fixture.write_text(text + "\n")
                run_env = {**env, **({"CODESIGN_IDENTITY": requested} if requested else {})}
                result = subprocess.run(command, env=run_env, capture_output=True, text=True)
                self.assertEqual(result.returncode, expected, result.stderr)
                if expected == 0:
                    self.assertEqual(result.stdout.strip(), requested or first)
            profile = ['/bin/bash', '-c', 'source "$1"; resolve_notary_profile "$2"',
                       'bash', str(UTILS), str(root)]
            self.assertEqual(subprocess.check_output(profile, env=env, text=True).strip(), "jpegdrop-notary")
            self.assertEqual(subprocess.check_output(profile, env={**env, "NOTARY_PROFILE": "fixture-profile"},
                                                    text=True).strip(), "fixture-profile")

    def test_app_verification_propagates_failures_and_checks_both_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "JPEGdrop.app"
            (app / "Contents").mkdir(parents=True)
            info = {"CFBundleIdentifier": "de.dm0.jpegdrop", "CFBundleVersion": "2.5.1",
                    "CFBundleShortVersionString": "2.5.1"}
            script = '''source "$1"
codesign() { [ "$FAILURE" != sign ]; }
xcrun() { [ "$FAILURE" != ticket ]; }
spctl() { [ "$FAILURE" != gatekeeper ]; }
if verify_notarized_app "$2" 2.5.1; then exit 0; else exit 8; fi
'''
            for field, value, failure, expected in [
                (None, None, "none", 0), (None, None, "sign", 8),
                (None, None, "ticket", 8), (None, None, "gatekeeper", 8),
                ("CFBundleIdentifier", "other.app", "none", 8),
                ("CFBundleShortVersionString", "2.4.0", "none", 8),
                ("CFBundleVersion", "2.4.0", "none", 8),
            ]:
                changed = {**info, **({field: value} if field else {})}
                with (app / "Contents/Info.plist").open("wb") as target:
                    plistlib.dump(changed, target)
                result = subprocess.run(['/bin/bash', '-c', script, 'bash', str(UTILS), str(app)],
                                        env={**os.environ, "FAILURE": failure}, capture_output=True, text=True)
                self.assertEqual(result.returncode, expected, result.stderr)


if __name__ == "__main__":
    unittest.main()
