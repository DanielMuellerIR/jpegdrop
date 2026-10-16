"""Release-Herkunft gegen einen echten, isolierten Git-Quellstand prüfen."""
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.build_manifest import require_clean_source, verify_release_source, write_manifest


class ManifestTests(unittest.TestCase):
    def test_dirty_changed_and_wrong_version_sources_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="jpegdrop-manifest-") as directory:
            root = Path(directory)
            repo = root / "source"
            repo.mkdir()
            def git(*args):
                subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
            git("init", "--quiet")
            git("config", "user.name", "JPEGdrop test")
            git("config", "user.email", "test@example.invalid")
            requirements = repo / "requirements.txt"
            requirements.write_text("pillow==12.2.0\n")
            git("add", "requirements.txt")
            git("commit", "--quiet", "-m", "fixture")
            app = root / "JPEGdrop.app"
            (app / "Contents/Resources").mkdir(parents=True)
            info = {"CFBundleIdentifier": "de.dm0.jpegdrop", "CFBundleVersion": "2.5.1",
                    "CFBundleShortVersionString": "2.5.1", "LSMinimumSystemVersion": "12.0"}
            with (app / "Contents/Info.plist").open("wb") as f:
                plistlib.dump(info, f)
            write_manifest(repo, app, "3.14.6", "20260728")
            verified = verify_release_source(repo, app, "2.5.1")
            self.assertTrue(verified["source"]["clean"])
            requirements.write_text("pillow==changed\n")
            with self.assertRaisesRegex(ValueError, "sauberen"):
                require_clean_source(repo)
            with self.assertRaises(ValueError):
                verify_release_source(repo, app, "2.5.1")
            git("add", "requirements.txt")
            git("commit", "--quiet", "-m", "second fixture")
            with self.assertRaisesRegex(ValueError, "Quellcommit"):
                verify_release_source(repo, app, "2.5.1")
            write_manifest(repo, app, "3.14.6", "20260728")
            with self.assertRaises(ValueError):
                verify_release_source(repo, app, "2.5.2")
            verify_release_source(repo, app, "2.5.1")
            (repo / "untracked.py").write_text("new source\n")
            with self.assertRaises(ValueError):
                verify_release_source(repo, app, "2.5.1")


if __name__ == "__main__":
    unittest.main()
