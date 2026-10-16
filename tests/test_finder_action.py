"""Schnellaktionsschutz ausschließlich mit temporären Services-Verzeichnissen."""
import fcntl
import json
import os
from pathlib import Path
import plistlib
import subprocess
from unittest.mock import patch

import pytest

from app.finder_action import FinderAction, SCRIPT, WORKFLOW_NAME, verify_installed_app, rename_exclusive


@pytest.fixture
def action(tmp_path):
    with patch('app.finder_action.verify_installed_app'):
        yield FinderAction(tmp_path / 'Services')


def test_install_repeat_remove_and_file_arguments(action):
    action.install()
    before = {str(p.relative_to(action.path)): p.read_bytes() for p in action.path.rglob('*') if p.is_file()}
    workflow = plistlib.loads((action.path / 'Contents/document.wflow').read_bytes())
    assert workflow['actions'][0]['action']['ActionParameters']['inputMethod'] == 1
    assert workflow['workflowMetaData']['serviceInputTypeIdentifier'] == 'com.apple.Automator.fileSystemObject'
    assert 'bereits eingerichtet' in action.install()
    assert {str(p.relative_to(action.path)): p.read_bytes() for p in action.path.rglob('*') if p.is_file()} == before
    unrelated = action.services / 'Foreign.workflow'; unrelated.mkdir()
    (unrelated / 'user-file').write_bytes(b'keep')
    action.remove()
    assert not action.path.exists()
    assert (unrelated / 'user-file').read_bytes() == b'keep'
    assert 'nicht eingerichtet' in action.remove()


def test_modified_foreign_unknown_and_symlink_workflows_survive(action, tmp_path):
    action.install()
    target = action.path / 'Contents/document.wflow'
    target.write_bytes(target.read_bytes() + b'\nmanual change')
    before = target.read_bytes()
    for operation in (action.install, action.remove):
        with pytest.raises(ValueError, match='fremd oder wurde geändert'):
            operation()
        assert target.read_bytes() == before
    target.write_bytes(b'foreign')
    (action.path / 'Contents/jpegdrop-receipt.json').unlink()
    with pytest.raises(ValueError):
        action.remove()
    assert target.read_bytes() == b'foreign'
    other = FinderAction(tmp_path / 'SymlinkServices'); other.services.mkdir()
    other.path.symlink_to(action.path, target_is_directory=True)
    with pytest.raises(ValueError):
        other.remove()
    assert other.path.is_symlink() and target.read_bytes() == b'foreign'


def test_extra_files_and_directories_are_preserved(action):
    action.install()
    extra = action.path / 'Manual'; extra.mkdir(); (extra / 'data').write_bytes(b'manual')
    with pytest.raises(ValueError):
        action.remove()
    assert (extra / 'data').read_bytes() == b'manual'


def test_concurrent_install_cannot_replace_even_empty_foreign_directory(tmp_path):
    source = tmp_path / 'source'; source.mkdir()
    target = tmp_path / 'foreign'; target.mkdir()
    with pytest.raises(FileExistsError):
        rename_exclusive(source, target)
    assert source.is_dir() and target.is_dir()


def test_busy_installation_and_failed_app_check_do_not_mutate(action):
    action.services.mkdir()
    lock = action.services / '.jpegdrop-finder.lock'
    with lock.open('w') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match='gerade bearbeitet'):
            action.install()
    assert not action.path.exists()
    with patch('app.finder_action.verify_installed_app', side_effect=ValueError('invalid app')):
        with pytest.raises(ValueError, match='invalid app'):
            action.install()
    assert not action.path.exists()


def test_app_requires_signature_ticket_and_gatekeeper():
    for failed in range(3):
        values = [subprocess.CompletedProcess([], int(i == failed)) for i in range(failed + 1)]
        with patch('app.finder_action.subprocess.run', side_effect=values) as run:
            with pytest.raises(ValueError, match='notarisierte'):
                verify_installed_app()
            assert run.call_count == failed + 1


def test_shell_preserves_special_paths_and_never_evaluates_them(tmp_path):
    capture = tmp_path / 'argv.json'
    helper = tmp_path / 'open-stub'
    helper.write_text('#!' + os.path.realpath(os.sys.executable) + '\nimport json,sys\n'
                      + 'open(' + repr(str(capture)) + ',"w").write(json.dumps(sys.argv[1:]))\n')
    helper.chmod(0o700)
    script = SCRIPT.replace('/usr/bin/codesign', '/usr/bin/true').replace('/usr/bin/xcrun', '/usr/bin/true')
    script = script.replace('/usr/sbin/spctl', '/usr/bin/true').replace('/usr/bin/open', str(helper))
    arguments = ['-leading.png', '/tmp/space quote " café.png', '/tmp/new\nline.png',
                 '$(touch ' + str(tmp_path / 'injected') + ')', '/tmp/`echo nope`/folder']
    subprocess.run(['/bin/bash', '-c', script, 'workflow', *arguments], check=True, timeout=5)
    assert json.loads(capture.read_text()) == ['-g', '-a', '/Applications/JPEGdrop.app', '--', *arguments]
    assert not (tmp_path / 'injected').exists()
