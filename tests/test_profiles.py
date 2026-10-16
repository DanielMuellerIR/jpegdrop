"""Profilgrundlage: Definitionen, Dateischutz und konkurrierende Schreiber."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import json
import os
import fcntl
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.profiles import ProfileStore, ProfileError, start_profiles, active_profile, apply_profile
import jpegdrop


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.path = self.root / 'nested/profiles.json'
        self.store = ProfileStore(self.path)

    def tearDown(self):
        self.directory.cleanup()

    def test_read_does_not_create_directory_and_definitions_are_immutable(self):
        self.assertEqual([p.name for p in self.store.list()], ['Mail', 'Website-Fotos', 'Screenshots'])
        self.assertFalse(self.path.parent.exists())
        profile = self.store.create(' Café ', {'codec':'png','preserve_alpha':True,'target_bytes':5000})
        self.assertEqual(profile.name,'Café')
        self.assertEqual(profile.settings()['preset'],'verlustfrei')
        settings=profile.settings();settings['target_bytes']=1
        self.assertEqual(profile.settings()['target_bytes'],5000)
        with self.assertRaises(FrozenInstanceError):profile.name='changed'
        before=self.path.read_bytes();modified=self.path.stat().st_mtime_ns
        self.assertEqual(self.store.by_name('CAFE\u0301').id,profile.id)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(self.path.stat().st_mtime_ns,modified)
        self.assertEqual(self.path.stat().st_mode & 0o777,0o600)

    def test_rename_update_delete_and_duplicate_names(self):
        first=self.store.create('Web',{'codec':'webp','preset':'verlustfrei'})
        second=self.store.create('Photo',{'codec':'jpeg'})
        with self.assertRaises(ProfileError):self.store.create('WEB',{'codec':'png'})
        with self.assertRaises(ProfileError):self.store.update(second.id,name='web')
        changed=self.store.update(first.id,name='Web klein',options={'codec':'webp','max_edge':800})
        self.assertEqual(changed.id,first.id)
        self.assertEqual(changed.settings()['max_edge'],800)
        self.assertEqual(first.settings()['max_edge'],0)
        self.store.delete(first.id)
        self.assertEqual([p.id for p in self.store.list()],[p.id for p in start_profiles()] + [second.id])
        with self.assertRaises(ProfileError):self.store.delete(first.id)

    def test_invalid_and_future_data_are_never_replaced(self):
        self.path.parent.mkdir()
        for raw in [b'broken JSON',b'{"schema":2,"profiles":[]}',b'{"schema":true,"profiles":[]}',
                    b'{"schema":1,"profiles":[],"future":"keep"}']:
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                with self.assertRaises(ProfileError):self.store.create('New',{'codec':'jpeg'})
                self.assertEqual(self.path.read_bytes(),raw)

    def test_invalid_options_and_control_names_have_no_side_effects(self):
        for options in [{'codec':[]},{'codec':'png','preset':[]},{'target_bytes':True},
                        {'codec':'jpeg','preserve_alpha':True},{'future':1}]:
            with self.subTest(options=options),self.assertRaises(ProfileError):self.store.create('Test',options)
        for name in ['', 'x'*65, 'bad\nname']:
            with self.subTest(name=name),self.assertRaises(ProfileError):self.store.create(name,{'codec':'png'})
        self.assertFalse(self.path.exists())

    def test_concurrent_stores_preserve_both_definitions(self):
        def create(name):return ProfileStore(self.path).create(name,{'codec':'jpeg'})
        with ThreadPoolExecutor(max_workers=2) as pool:
            profiles=list(pool.map(create,['One','Two']))
        self.assertEqual({p.id for p in self.store.list()},{p.id for p in (*start_profiles(), *profiles)})

    def test_changed_data_aborts_save_and_keeps_newer_content(self):
        self.store.create('First',{'codec':'jpeg'})
        future=b'{"schema":2,"profiles":[],"note":"newer editor"}'
        class ChangedStore(ProfileStore):
            def _save(inner,profiles,original):
                inner.path.write_bytes(future)
                return super()._save(profiles,original)
        with self.assertRaisesRegex(ProfileError,'inzwischen geändert'):
            ChangedStore(self.path).create('Second',{'codec':'png'})
        self.assertEqual(self.path.read_bytes(),future)
        self.assertEqual(list(self.path.parent.glob('.profiles-*')),[])

    def test_failed_atomic_replace_preserves_library_and_removes_only_own_temp(self):
        self.store.create('First',{'codec':'jpeg'});before=self.path.read_bytes()
        foreign=self.path.parent/'.profiles-foreign';foreign.write_bytes(b'keep')
        with patch('app.profiles.os.replace',side_effect=OSError('controlled write error')),self.assertRaises(OSError):
            self.store.create('Second',{'codec':'png'})
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(foreign.read_bytes(),b'keep')
        self.assertEqual(list(self.path.parent.glob('.profiles-*')),[foreign])

    def test_limit_and_symlink_preserve_existing_data(self):
        with patch('app.profiles.MAX_PROFILES',len(start_profiles()) + 2):
            self.store.create('One',{'codec':'jpeg'});self.store.create('Two',{'codec':'png'})
            before=self.path.read_bytes()
            with self.assertRaises(ProfileError):self.store.create('Three',{'codec':'avif'})
            self.assertEqual(self.path.read_bytes(),before)
        alias=self.root/'alias.json';alias.symlink_to(self.path)
        with self.assertRaises(ProfileError):ProfileStore(alias).create('Link',{'codec':'jpeg'})
        self.assertTrue(alias.is_symlink());self.assertEqual(self.path.read_bytes(),before)

    def test_busy_library_has_bounded_wait_and_keeps_existing_data(self):
        self.store.create('First',{'codec':'jpeg'});before=self.path.read_bytes()
        descriptor=os.open(self.path.with_suffix('.json.lock'),os.O_RDWR)
        try:
            fcntl.flock(descriptor,fcntl.LOCK_EX)
            with patch('app.profiles.LOCK_TIMEOUT',0.02),self.assertRaisesRegex(ProfileError,'gerade bearbeitet'):
                self.store.create('Second',{'codec':'png'})
            self.assertEqual(self.path.read_bytes(),before)
        finally:os.close(descriptor)

    def test_stale_editor_cannot_overwrite_or_delete_changed_definition(self):
        profile = self.store.create('Sondername / „Web“ $(true)', {'codec': 'png'})
        updated = self.store.update(profile.id, options={'codec': 'webp'}, expected=profile)
        original = self.path.read_bytes()
        with self.assertRaisesRegex(ProfileError, 'inzwischen geändert'):
            self.store.update(profile.id, name='stale', expected=profile)
        with self.assertRaisesRegex(ProfileError, 'inzwischen geändert'):
            self.store.delete(profile.id, expected=profile)
        self.assertEqual(self.path.read_bytes(), original)
        self.store.delete(updated.id, expected=updated)

    def test_deleted_starters_stay_deleted_and_empty_library_is_not_reseeded(self):
        for profile in self.store.list():
            self.store.delete(profile.id, expected=profile)
        self.assertEqual(ProfileStore(self.path).list(), ())

    def test_active_identity_tracks_rename_and_detects_changes_without_mutating_settings(self):
        profile = self.store.list()[0]
        original = dict(jpegdrop.DEFAULT_SETTINGS)
        settings = apply_profile(original, profile)
        self.assertEqual(original, jpegdrop.DEFAULT_SETTINGS)
        self.assertEqual(active_profile(settings, self.store.list()), profile)
        renamed = self.store.update(profile.id, name='Mail umbenannt', expected=profile)
        self.assertEqual(active_profile(settings, self.store.list()).name, renamed.name)
        self.assertIsNone(active_profile({**settings, 'max_edge': 1}, self.store.list()))
        self.store.delete(renamed.id)
        self.assertIsNone(active_profile(settings, self.store.list()))


if __name__=='__main__':unittest.main()
