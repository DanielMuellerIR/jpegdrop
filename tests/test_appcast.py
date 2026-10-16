"""Appcast-Zielbindung und Schlüsseltrennung vor der realen Signaturprobe prüfen."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from create_appcast import sign_appcast
from verify_release_dmg import mounted_device, run


class AppcastTests(unittest.TestCase):
    def test_ticket_network_failure_is_retried_but_rejection_is_not(self):
        network = subprocess.CalledProcessError(68, ['xcrun'], output=b'NSURLErrorDomain Code=-1005 connection lost')
        with patch('verify_release_dmg.subprocess.check_output', side_effect=[network, b'accepted']) as call, \
                patch('verify_release_dmg.time.sleep') as sleep:
            self.assertEqual(run('/usr/bin/xcrun', 'stapler', 'validate', '/fixture.app'), b'accepted')
            self.assertEqual(call.call_count, 2)
            sleep.assert_called_once_with(2)
        rejection = subprocess.CalledProcessError(68, ['xcrun'], output=b'Ticket is invalid')
        with patch('verify_release_dmg.subprocess.check_output', side_effect=rejection) as call, \
                patch('verify_release_dmg.time.sleep') as sleep, self.assertRaises(ValueError):
            run('/usr/bin/xcrun', 'stapler', 'validate', '/fixture.app')
        self.assertEqual(call.call_count, 1)
        sleep.assert_not_called()

    def test_mount_device_selection_uses_exact_private_mount(self):
        info = {'images': [{'system-entities': [
            {'mount-point': '/other/mount', 'dev-entry': '/dev/foreign'},
            {'mount-point': '/own/mount', 'dev-entry': '/dev/own'}]}]}
        self.assertEqual(mounted_device(info, Path('/own/mount')), '/dev/own')
        self.assertIsNone(mounted_device(info, Path('/missing')))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            actual = root / 'actual'
            actual.mkdir()
            alias = root / 'alias'
            alias.symlink_to(actual, target_is_directory=True)
            info = {'system-entities': [{'mount-point': str(actual), 'dev-entry': '/dev/own'}]}
            self.assertEqual(mounted_device(info, alias), '/dev/own')

    def test_wrong_feed_target_is_rejected_and_secret_is_not_in_child_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            update = root / 'update'
            update.mkdir()
            (update / 'JPEGdrop-2.6.1.dmg').write_bytes(b'dmg')
            site = root / 'site'
            invocations = []
            def tool(args, **kwargs):
                self.assertNotIn('SPARKLE_PRIVATE_KEY', os.environ)
                self.assertEqual(kwargs['input'], b'fixture')
                self.assertNotIn('fixture', args)
                invocations.append(args)
                if Path(args[0]).name == 'generate_appcast':
                    (site / 'appcast.xml').write_text('<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">'
                        '<channel><item><sparkle:version>2.6.1</sparkle:version>'
                        '<enclosure url="https://example.com/wrong.dmg" length="3" sparkle:edSignature="fixture-signature"/>'
                        '</item></channel></rss>')
                return subprocess.CompletedProcess(args, 0, b'', b'')
            with patch.dict(os.environ, {'SPARKLE_PRIVATE_KEY': 'fixture'}), \
                    patch('create_appcast.subprocess.run', side_effect=tool), self.assertRaises(ValueError):
                sign_appcast(root / 'tools', update, site, 'example/jpegdrop', 'v2.6.1')
            self.assertEqual(len(invocations), 1)

    def test_tool_errors_do_not_echo_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            update = root / 'update'
            update.mkdir()
            (update / 'JPEGdrop-2.6.1.dmg').write_bytes(b'dmg')
            with patch.dict(os.environ, {'SPARKLE_PRIVATE_KEY': 'fixture'}), \
                    patch('create_appcast.subprocess.run', return_value=subprocess.CompletedProcess([], 1, b'fixture', b'fixture')):
                with self.assertRaises(ValueError) as caught:
                    sign_appcast(root / 'tools', update, root / 'site', 'example/jpegdrop', 'v2.6.1')
                self.assertNotIn('fixture', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
