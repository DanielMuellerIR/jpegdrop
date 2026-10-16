"""Ein echtes arm64-Programm mit zu hoher Untergrenze und Intel-only ablehnen."""
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.check_compatibility import inspect_app


class CompatibilityTests(unittest.TestCase):
    def test_native_deployment_target_and_architecture(self):
        with tempfile.TemporaryDirectory(prefix="jpegdrop-compatibility-") as directory:
            root = Path(directory)
            app = root / "JPEGdrop.app"
            (app / "Contents/MacOS").mkdir(parents=True)
            with (app / "Contents/Info.plist").open("wb") as target:
                plistlib.dump({"LSMinimumSystemVersion": "12.0"}, target)
            code = root / "fixture.c"
            code.write_text("int fixture(void) { return 1; }\n")
            binary = app / "Contents/MacOS/fixture.dylib"
            def compile(architecture, minimum):
                subprocess.run(["/usr/bin/xcrun", "clang", "-dynamiclib", "-arch", architecture,
                                "-mmacosx-version-min=" + minimum, str(code), "-o", str(binary)],
                               capture_output=True, check=True)
            compile("arm64", "11.0")
            self.assertEqual(inspect_app(app)["nativeFiles"], 1)
            compile("arm64", "13.0")
            with self.assertRaisesRegex(ValueError, "benötigt macOS 13"):
                inspect_app(app)
            compile("x86_64", "12.0")
            with self.assertRaisesRegex(ValueError, "arm64"):
                inspect_app(app)


if __name__ == "__main__":
    unittest.main()
