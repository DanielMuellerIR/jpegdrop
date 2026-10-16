"""Quellpakete müssen ihre Prüfsummen erfüllen und vorhandene Ziele schützen."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from tools.package_sources import package_sources, verify_package


class DependencySourceTests(unittest.TestCase):
    def test_checked_archive_and_notices_are_packaged_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            licenses = root / 'licenses'
            licenses.mkdir()
            data = b'corresponding library source archive'
            sources = [{'name': 'library', 'version': '1.0', 'url': 'https://example.com/library.tar.gz',
                        'sha256': hashlib.sha256(data).hexdigest()}]
            (licenses / 'sources.json').write_text(json.dumps(sources))
            (licenses / 'NOTICE.txt').write_text('required notice')
            output = root / 'dist/sources.tar.gz'
            with patch('tools.package_sources.urllib.request.urlopen', return_value=io.BytesIO(data)):
                digest = package_sources(root, output)
            self.assertEqual(digest, hashlib.sha256(output.read_bytes()).hexdigest())
            verify_package(root, output)
            (licenses / 'NOTICE.txt').write_text('changed notice')
            with self.assertRaises(ValueError):
                verify_package(root, output)
            with tarfile.open(output) as archive:
                self.assertEqual(archive.extractfile('library-1.0.tar.gz').read(), data)
                self.assertEqual(archive.extractfile('Licenses/NOTICE.txt').read(), b'required notice')
            before = output.read_bytes()
            with patch('tools.package_sources.urllib.request.urlopen') as download, self.assertRaises(ValueError):
                package_sources(root, output)
            download.assert_not_called()
            self.assertEqual(output.read_bytes(), before)

    def test_wrong_hash_leaves_no_source_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'licenses').mkdir()
            (root / 'licenses/sources.json').write_text(json.dumps([{'name': 'library', 'version': '1',
                'url': 'https://example.com/library.tar.gz', 'sha256': '0' * 64}]))
            output = root / 'sources.tar.gz'
            with patch('tools.package_sources.urllib.request.urlopen', return_value=io.BytesIO(b'corrupt')), self.assertRaises(ValueError):
                package_sources(root, output)
            self.assertFalse(output.exists())
            self.assertFalse(list(root.glob('.jpegdrop-sources-*')))


if __name__ == '__main__':
    unittest.main()
