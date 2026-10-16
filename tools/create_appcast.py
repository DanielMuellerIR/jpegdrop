#!/usr/bin/env python3
"""Sparkle-Werkzeuge getrennt bereitstellen und einen geprüften Appcast signieren."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import re
import subprocess
import xml.etree.ElementTree as E

from sparkle import verified_archive, FRAMEWORK_PATH, VERSION


def prepare_tools(cache: Path, destination: Path) -> None:
    if destination.exists():
        raise ValueError('Werkzeugziel existiert bereits.')
    destination.mkdir(parents=True)
    subprocess.run(['/usr/bin/ditto', '-x', '-k', str(verified_archive(cache)), str(destination)], check=True)
    info = plistlib.loads((destination / FRAMEWORK_PATH / 'Resources/Info.plist').read_bytes())
    if info['CFBundleShortVersionString'] != VERSION:
        raise ValueError('Unerwartete Sparkle-Werkzeugversion.')


def sign_appcast(tools: Path, update: Path, site: Path, repository: str, tag: str) -> None:
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository) or not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise ValueError('Ungültiges Repository oder Release-Tag.')
    archives = list(update.glob('*.dmg'))
    if len(archives) != 1 or archives[0].name != f'JPEGdrop-{tag[1:]}.dmg':
        raise ValueError('Genau das Release-DMG wird als Update erwartet.')
    private_key = os.environ.pop('SPARKLE_PRIVATE_KEY', '').encode()
    if not private_key:
        raise ValueError('Sparkle-Schlüssel fehlt.')
    site.mkdir(parents=True, exist_ok=True)
    feed = site / 'appcast.xml'
    prefix = f'https://github.com/{repository}/releases/download/{tag}/'
    def invoke(name, *args):
        result = subprocess.run([str(tools / 'bin' / name), '--ed-key-file', '-', *map(str, args)],
                                input=private_key, capture_output=True)
        if result.returncode:
            # Werkzeugfehler dürfen den über stdin gelieferten Schlüssel nicht
            # versehentlich in CI-Logs weiterreichen.
            raise ValueError(f'Sparkle-{name} fehlgeschlagen (Status {result.returncode}).')
    invoke('generate_appcast', '--embed-release-notes', '--maximum-versions', '1', '--maximum-deltas', '0',
           '--download-url-prefix', prefix, '--link', f'https://github.com/{repository}', '-o', feed, update)
    xml = E.parse(feed)
    ns = {'sparkle': 'http://www.andymatuschak.org/xml-namespaces/sparkle'}
    items = xml.findall('./channel/item')
    if len(items) != 1:
        raise ValueError('Appcast enthält nicht genau ein Vollupdate.')
    item = items[0]
    enclosure = item.find('enclosure')
    if (item.findtext('sparkle:version', namespaces=ns) != tag[1:]
            or enclosure is None or enclosure.get('url') != prefix + archives[0].name
            or enclosure.get('length') != str(archives[0].stat().st_size)
            or enclosure.get('{'+ns['sparkle']+'}deltaFrom')):
        raise ValueError('Appcast-Version, URL, Größe oder Vollupdate stimmen nicht.')
    signature = enclosure.get('{'+ns['sparkle']+'}edSignature')
    if not signature:
        raise ValueError('Archivsignatur fehlt.')
    invoke('sign_update', '--verify', archives[0], signature)
    invoke('sign_update', '--disable-signing-warning', feed)
    invoke('sign_update', '--verify', feed)
    E.parse(feed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    prepare = actions.add_parser('prepare-tools')
    prepare.add_argument('--cache', required=True, type=Path)
    prepare.add_argument('--output', required=True, type=Path)
    sign = actions.add_parser('sign')
    for name in ('tools', 'update', 'site'):
        sign.add_argument('--'+name, required=True, type=Path)
    sign.add_argument('--repository', required=True)
    sign.add_argument('--tag', required=True)
    args = parser.parse_args()
    try:
        if args.action == 'prepare-tools':
            prepare_tools(args.cache, args.output)
        else:
            sign_appcast(args.tools, args.update, args.site, args.repository, args.tag)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(2, f'FEHLER: {error}\n')
    print('Sparkle-Werkzeuge geprüft.' if args.action == 'prepare-tools' else 'Archiv und Feed signiert und verifiziert.')


if __name__ == '__main__':
    main()
