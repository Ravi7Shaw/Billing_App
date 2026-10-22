"""Update safeguards with a real temporary Git remote and synthetic Windows builds."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import json
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/os.environ.get('BILLING_APP_DIR','app')))
import updater
from updater import UpdateError, check_update, apply_update, start_update, find_python


class UpdaterTests(unittest.TestCase):
    def run_git(self,where,*args):
        result=subprocess.run(['git','-C',str(where),*args],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        return result.stdout.strip()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
        self.remote=self.folder/'remote.git';self.remote.mkdir()
        self.run_git(self.remote,'init','--bare')
        self.root=self.folder/'Shop & Accounts';self.root.mkdir()
        self.run_git(self.root,'init','-b','main')
        self.run_git(self.root,'config','user.name','Synthetic Test')
        self.run_git(self.root,'config','user.email','test@example.invalid')
        self.app=self.root/'app';self.app.mkdir()
        (self.app/'main.py').write_text('old source')
        (self.root/'.gitignore').write_text('dist/\nbuild/\n')
        self.run_git(self.root,'add','.');self.run_git(self.root,'commit','-m','Initial fixture')
        self.run_git(self.root,'remote','add','origin',str(self.remote))
        self.run_git(self.root,'push','-u','origin','main')
        self.writer=self.folder/'writer'
        self.run_git(self.folder,'clone','-b','main',str(self.remote),str(self.writer))
        self.run_git(self.writer,'config','user.name','Synthetic Test')
        self.run_git(self.writer,'config','user.email','test@example.invalid')
        (self.app/'dist').mkdir();self.exe=self.app/'dist/ClothShopBilling.exe';self.exe.write_bytes(b'MZ old executable')

    def new_remote(self):
        (self.writer/'app/main.py').write_text('new source')
        self.run_git(self.writer,'add','.');self.run_git(self.writer,'commit','-m','Remote update')
        self.run_git(self.writer,'push','origin','main')

    @staticmethod
    def build(root,app,stage): (stage/'ClothShopBilling.exe').write_bytes(b'MZ new executable')

    def test_fast_forward_build_replace_and_keep_previous(self):
        self.new_remote();plan=check_update(self.root,self.app)
        self.assertEqual(plan['count'],1)
        self.assertEqual((self.app/'main.py').read_text(),'old source')
        self.assertEqual(apply_update(plan,self.build),self.exe)
        self.assertEqual(self.exe.read_bytes(),b'MZ new executable')
        self.assertEqual(self.exe.with_name('ClothShopBilling.previous.exe').read_bytes(),b'MZ old executable')
        self.assertEqual(self.run_git(self.root,'branch','--show-current'),'main')
        self.assertEqual(check_update(self.root,self.app)['count'],0)

    def test_dirty_checkout_and_diverged_history_are_not_overwritten(self):
        (self.app/'main.py').write_text('local edits')
        with self.assertRaisesRegex(UpdateError,'local changes'):check_update(self.root,self.app)
        self.run_git(self.root,'add','.');self.run_git(self.root,'commit','-m','Local work')
        self.new_remote()
        with self.assertRaisesRegex(UpdateError,'history differ'):check_update(self.root,self.app)
        self.assertEqual((self.app/'main.py').read_text(),'local edits')

    def test_build_failure_keeps_working_executable(self):
        self.new_remote();plan=check_update(self.root,self.app)
        def fail(*args):raise RuntimeError('Synthetic build failure')
        with self.assertRaisesRegex(RuntimeError,'build failure'):apply_update(plan,fail)
        self.assertEqual(self.exe.read_bytes(),b'MZ old executable')
        self.assertEqual(list((self.app/'build').iterdir()),[])

    def test_checkout_change_after_check_is_rejected(self):
        plan=check_update(self.root,self.app)
        (self.app/'main.py').write_text('Changed during confirmation')
        with self.assertRaisesRegex(UpdateError,'checkout changed'):apply_update(plan,self.build)
        self.assertEqual(self.exe.read_bytes(),b'MZ old executable')

    def test_detached_helper_uses_checkout_source_in_frozen_app(self):
        (self.app/'updater.py').write_text('# synthetic standalone helper')
        plan = dict(root=str(self.root), app_dir=str(self.app))
        helper = self.folder/'billing-update-test'; helper.mkdir()
        with patch.object(updater.tempfile, 'mkdtemp', return_value=str(helper)), \
             patch.object(updater.subprocess, 'Popen') as launch, \
             patch.object(updater.subprocess, 'CREATE_NEW_CONSOLE', 16, create=True), \
             patch.object(updater, '__file__', str(self.folder/'missing-frozen-module.py')):
            start_update(plan, ['python'])
        self.assertEqual((helper/'updater.py').read_text(), '# synthetic standalone helper')
        self.assertEqual(json.loads((helper/'plan.json').read_text())['root'], str(self.root))
        self.assertEqual(launch.call_args.args[0][1], str(helper/'updater.py'))

    def test_rebuild_uses_base_python_outside_the_app_environment(self):
        base = str(self.folder/'System Python'/'python.exe')
        results = [subprocess.CompletedProcess([],0), subprocess.CompletedProcess([],0,stdout=base+'\n')]
        with patch.object(updater.shutil, 'which', return_value='py'), \
             patch.object(updater.subprocess, 'run', side_effect=results):
            self.assertEqual(find_python(self.root), [base])

    def test_invalid_build_output_is_rejected(self):
        plan=check_update(self.root,self.app)
        def invalid(root,app,stage):(stage/'ClothShopBilling.exe').write_bytes(b'not executable')
        with self.assertRaisesRegex(UpdateError,'Windows executable'):apply_update(plan,invalid)
        self.assertEqual(self.exe.read_bytes(),b'MZ old executable')
