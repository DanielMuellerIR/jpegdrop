"""Mount-Rücknahme darf fremde Images weder aushängen noch ihre Dateien löschen."""
from pathlib import Path
import os
import plistlib
import subprocess
import tempfile
import unittest

RELEASE = Path(__file__).resolve().parents[1] / 'release.sh'


class ReleaseCleanupTests(unittest.TestCase):
    def run_cleanup(self, *, known_device=False, own_in_info=True, fail_detach=False):
        source = RELEASE.read_text()
        functions = source[source.index('detach_own_device()'):source.index('# Alles, was fehlen kann')]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / 'own-stage'
            stage.mkdir()
            mount = stage / 'mount'
            own = {'dev-entry': '/dev/diskFIXTUREs1', 'mount-point': str(mount)}
            foreign = {'dev-entry': '/dev/diskFOREIGNs1', 'mount-point': str(root / 'foreign-mount')}
            info = {'images': [{'system-entities': [foreign] + ([own] if own_in_info else [])}]}
            fixture = root / 'hdiutil-info.plist'
            fixture.write_bytes(plistlib.dumps(info))
            trace = root / 'trace'
            script = functions + '''
STAGE="$ROOT/own-stage"
MOUNT_DIR="$STAGE/mount"
ATTACH_PLIST="$STAGE/missing-attach.plist"
DMG_ATTACH_STATE=attaching
DMG_DEVICE="$KNOWN"
hdiutil() {
  if [ "$1" = info ]; then cat "$INFO"; return 0; fi
  if [ "$1" = detach ]; then printf 'detach:%s\n' "$2" >> "$TRACE"; [ "$FAIL_DETACH" = 0 ]; return $?; fi
  return 99
}
sleep() { :; }
rm() { printf 'remove:%s\n' "${*: -1}" >> "$TRACE"; }
aufraeumen
aufraeumen
printf '%s|%s|%s\n' "$DMG_ATTACH_STATE" "$STAGE" "$DMG_DEVICE"
trap - EXIT
'''
            env = {**os.environ, 'ROOT': str(root), 'INFO': str(fixture), 'TRACE': str(trace),
                   'KNOWN': '/dev/diskFIXTUREs1' if known_device else '',
                   'FAIL_DETACH': '1' if fail_detach else '0'}
            result = subprocess.run(['/bin/bash', '-c', script], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            actions = trace.read_text().splitlines() if trace.exists() else []
            self.assertNotIn('diskFOREIGN', '\n'.join(actions))
            return result.stdout.strip(), actions, result.stderr, str(stage)

    def test_signal_gap_recovers_only_own_mount_and_cleanup_is_idempotent(self):
        state, actions, _, stage = self.run_cleanup()
        self.assertEqual(state, 'none||')
        self.assertEqual(actions, ['detach:/dev/diskFIXTUREs1', 'remove:' + stage])

    def test_failed_detach_preserves_stage_and_never_removes_mount(self):
        state, actions, warnings, stage = self.run_cleanup(known_device=True, fail_detach=True)
        self.assertEqual(state, f'attaching|{stage}|/dev/diskFIXTUREs1')
        self.assertTrue(actions)
        self.assertTrue(all(action == 'detach:/dev/diskFIXTUREs1' for action in actions))
        self.assertIn('ließ sich nicht aushängen', warnings)

    def test_unknown_device_preserves_stage_and_foreign_images(self):
        state, actions, warnings, stage = self.run_cleanup(own_in_info=False)
        self.assertEqual(state, f'attaching|{stage}|')
        self.assertEqual(actions, [])
        self.assertIn('Device des eigenen Mountpunkts blieb unbekannt', warnings)


if __name__ == '__main__':
    unittest.main()
