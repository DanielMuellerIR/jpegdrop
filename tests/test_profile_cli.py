"""Profile als CLI-Basis: echte Ausgaben, Overrides und schreibfreies Lesen."""
import json
import os
from pathlib import Path
import subprocess
import sys

from PIL import Image
import pytest

import jpegdrop
from app.profiles import ProfileStore


@pytest.fixture
def cli_home(tmp_path):
    settings = tmp_path / 'Library/Application Support/JPEGdrop'
    settings.mkdir(parents=True)
    (settings / 'settings.json').write_text(json.dumps({**jpegdrop.DEFAULT_SETTINGS, 'codec': 'avif', 'max_edge': 3}))
    store = ProfileStore(settings / 'profiles.json')
    store.create('Café / -- „$(touch nope)“', {
        'codec': 'webp', 'preset': 'verlustfrei', 'max_edge': 12, 'preserve_alpha': True,
        'recursive': True, 'output_mode': 'subfolder', 'output_subdir': 'Profile output',
        'target_bytes': 10000, 'allow_target_resize': True, 'minimum_target_edge': 4,
    })
    return tmp_path, settings


def run_cli(home, *args):
    return subprocess.run([sys.executable, str(Path(jpegdrop.__file__).resolve()), *args],
        env={**os.environ, 'HOME': str(home)}, capture_output=True, text=True, timeout=15)


def test_profile_list_names_options_and_failed_lookup_do_not_write(cli_home):
    home, settings = cli_home
    before = {p.name: p.read_bytes() for p in settings.iterdir()}
    result = run_cli(home, '--list-profiles', '--json')
    assert result.returncode == 0, result.stderr
    assert [p['name'] for p in json.loads(result.stdout)][-1] == 'Café / -- „$(touch nope)“'
    result = run_cli(home, '--profile=missing', 'input.png')
    assert result.returncode == 2 and 'Profil nicht gefunden' in result.stderr
    assert {p.name: p.read_bytes() for p in settings.iterdir()} == before


def test_profile_overrides_settings_and_explicit_flags_override_profile(cli_home):
    home, settings = cli_home
    source = home / '- Bild „test“ $.png'
    Image.new('RGBA', (24, 16), (12, 34, 56, 70)).save(source)
    original = source.read_bytes()
    profile = 'CAFÉ / -- „$(touch nope)“'
    before = {p.name: p.read_bytes() for p in settings.iterdir()}
    result = run_cli(home, '--profile', profile, '--json', str(source))
    assert result.returncode == 0, result.stderr
    row = json.loads(result.stdout)[0]
    assert row['codec'] == 'webp' and row['preset'] == 'verlustfrei'
    assert row['output_width'] == 12 and row['alpha_preserved']
    assert Path(row['target']).parent == home / 'Profile output'
    nested = home / 'album/sub'; nested.mkdir(parents=True)
    Image.new('RGB', (20, 10), 'blue').save(nested / 'nested.png')
    result = run_cli(home, '--profile', profile, '--no-recursive', '--beside', '--codec', 'png',
        '--preset', 'verlustfrei', '--max-edge', '8', '--flatten-alpha', '--background', '#123456',
        '--target-size', '0', '--no-target-resize', '--target-min-edge', '2', '--json', str(source), str(home / 'album'))
    assert result.returncode == 0, result.stderr
    rows = json.loads(result.stdout)
    assert len([r for r in rows if r.get('target')]) == 1
    row = next(r for r in rows if r.get('target'))
    assert row['codec'] == 'png' and row['output_width'] == 8
    assert row['target_bytes'] == 0 and not row['alpha_preserved']
    assert Path(row['target']).parent == home
    assert Image.open(row['target']).getpixel((0, 0))[:3] != (12, 34, 56)
    assert source.read_bytes() == original
    assert {p.name: p.read_bytes() for p in settings.iterdir()} == before


def test_codec_fallback_legacy_quality_and_conflicting_explicit_flags(cli_home):
    home, _ = cli_home
    source = home / 'input.png'; Image.new('RGB', (24, 16), 'green').save(source)
    name = 'Café / -- „$(touch nope)“'
    result = run_cli(home, '--profile', name, '--codec', 'jpeg', '--json', str(source))
    assert result.returncode == 0, result.stderr
    row = json.loads(result.stdout)[0]
    assert row['codec'] == 'jpeg' and row['preset'] == 'hoch' and not row['alpha_preserved']
    result = run_cli(home, '--profile', name, '--quality', '42', '--json', str(source))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)[0]['quality'] == 42
    for flags in (('--codec', 'jpeg', '--preserve-alpha'), ('--quality', '40', '--preset', 'hoch'),
                  ('--quality', '40', '--codec', 'webp'), ('--codec', 'jpeg', '--preset', 'verlustfrei')):
        result = run_cli(home, '--profile', name, *flags, str(source))
        assert result.returncode != 0


def test_corrupt_future_and_unknown_libraries_are_preserved(cli_home):
    home, settings = cli_home
    library = settings / 'profiles.json'
    for raw in (b'{broken', b'{"schema":2,"profiles":[]}', b'{"schema":1,"profiles":[],"unknown":true}'):
        library.write_bytes(raw)
        result = run_cli(home, '--list-profiles')
        assert result.returncode == 2 and result.stderr
        result = run_cli(home, '--profile', 'Mail', str(home / 'input.png'))
        assert result.returncode == 2
        assert library.read_bytes() == raw
