#!/usr/bin/env python3
"""Release-DMG und Quellbezug prüfen, bevor ein Update-Schlüssel verwendet wird."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import signal
import subprocess
import tempfile
import time

from build_manifest import verify_release_source


def run(*args):
    return subprocess.check_output(list(map(str, args)), stderr=subprocess.STDOUT)


def mounted_device(info: dict, mount: Path) -> str | None:
    return next((entry['dev-entry'] for image in info.get('images', [info])
                 for entry in image.get('system-entities', [])
                 if entry.get('mount-point') and Path(entry['mount-point']).resolve() == mount.resolve()
                 and entry.get('dev-entry')), None)


def verify(repo: Path, dmg: Path, tag: str) -> dict:
    if not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise ValueError('Ungültiges Release-Tag.')
    version = tag[1:]
    if dmg.name != f'JPEGdrop-{version}.dmg':
        raise ValueError('DMG-Dateiname passt nicht zum Release-Tag.')
    commit = run('git', '-C', repo, 'rev-parse', '--verify', f'refs/tags/{tag}^{{commit}}').decode().strip()
    if commit != run('git', '-C', repo, 'rev-parse', 'HEAD').decode().strip():
        raise ValueError('Der Checkout muss genau dem Release-Tag entsprechen.')
    run('/usr/bin/codesign', '--verify', '--strict', dmg)
    run('/usr/bin/xcrun', 'stapler', 'validate', dmg)
    run('/usr/sbin/spctl', '--assess', '--type', 'open', '--context', 'context:primary-signature', dmg)
    run('/usr/bin/hdiutil', 'verify', dmg)
    work = Path(tempfile.mkdtemp(prefix='jpegdrop-dmg-check-')).resolve()
    mount = work / 'mount'
    mount.mkdir()
    device = None
    previous = {}
    def stop(signum, frame):
        raise SystemExit(128 + signum)
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous[signum] = signal.signal(signum, stop)
    try:
        info = plistlib.loads(run('/usr/bin/hdiutil', 'attach', dmg, '-readonly', '-nobrowse',
                                 '-noautoopen', '-mountpoint', mount, '-plist'))
        device = mounted_device(info, mount)
        if not device:
            raise ValueError('Eigenes DMG-Device konnte nicht bestimmt werden.')
        apps = list(mount.glob('*.app'))
        if len(apps) != 1 or apps[0].name != 'JPEGdrop.app' or apps[0].is_symlink():
            raise ValueError('DMG enthält nicht genau das erwartete App-Bundle.')
        app = apps[0]
        run('/usr/bin/codesign', '--verify', '--deep', '--strict', app)
        run('/usr/bin/xcrun', 'stapler', 'validate', app)
        run('/usr/sbin/spctl', '--assess', '--type', 'exec', app)
        manifest = verify_release_source(repo, app, version)
        info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
        expected = plistlib.loads((repo / 'app/Info.plist').read_bytes())
        if (info.get('JPEGdropTestUpdateFeed') or not info.get('SUFeedURL', '').startswith('https://')
                or info.get('SUFeedURL') != expected['SUFeedURL']
                or info.get('SUPublicEDKey') != expected['SUPublicEDKey']
                or not info.get('SURequireSignedFeed') or not info.get('SUVerifyUpdateBeforeExtraction')):
            raise ValueError('Update-Vertrauensanker oder Produktionsfeed weicht ab.')
        if not (mount / 'Licenses/sources.json').is_file():
            raise ValueError('Lizenzbestand fehlt im DMG.')
        return {'version': version, 'sourceCommit': manifest['source']['commit'], 'verified': True}
    finally:
        try:
            if device is None and os.path.ismount(mount):
                device = mounted_device(plistlib.loads(run('/usr/bin/hdiutil', 'info', '-plist')), mount)
            if device:
                for attempt in range(3):
                    result = subprocess.run(['/usr/bin/hdiutil', 'detach', device], capture_output=True)
                    if result.returncode == 0:
                        break
                    time.sleep(1)
            if os.path.ismount(mount):
                raise RuntimeError(f'Eigenes Image bleibt eingehängt; Arbeitsordner erhalten: {work}')
            shutil.rmtree(work)
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--dmg', type=Path, required=True)
    parser.add_argument('--tag', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.repo, args.dmg, args.tag)))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(2, f'FEHLER: Release-DMG-Prüfung fehlgeschlagen ({type(error).__name__}).\n')


if __name__ == '__main__':
    main()
