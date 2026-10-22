"""Platform-independent checks for the Windows installer and error paths."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('windows_setup', ROOT / 'tools' / 'windows_setup.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class FakeLog:
    def __init__(self, fail=None, output=True):
        self.calls = []
        self.fail = fail
        self.output = output

    def run(self, command, description, cwd):
        self.calls.append((command, description, cwd))
        if self.fail and self.fail in description:
            raise setup.SetupError('Synthetic ' + description + ' failure')
        if 'venv' in command:
            python = Path(command[-1]) / 'Scripts' / 'python.exe'
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_bytes(b'MZ fake interpreter')
        if 'PyInstaller' in command and self.output:
            exe = Path(command[command.index('--distpath')+1]) / 'ClothShopBilling.exe'
            exe.parent.mkdir(exist_ok=True)
            exe.write_bytes(b'MZ synthetic executable')


class WindowsSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='billing-setup-tests-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'Shop & Accounts !'
        self.app = self.root / 'app'
        (self.app / 'assets').mkdir(parents=True)
        for filename in ('main.py', 'requirements.txt', 'assets/icon.ico'):
            (self.app / filename).touch()

    def runtime(self, version=(3, 13), bits=8, machine='AMD64', implementation='CPython', gil=0):
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(setup.sys, 'platform', 'win32'))
        stack.enter_context(patch.object(setup.sys, 'version_info', version))
        stack.enter_context(patch.object(setup.sys, 'getwindowsversion', return_value=SimpleNamespace(major=10), create=True))
        stack.enter_context(patch.object(setup.platform, 'python_implementation', return_value=implementation))
        stack.enter_context(patch.object(setup.platform, 'machine', return_value=machine))
        stack.enter_context(patch.object(setup.struct, 'calcsize', return_value=bits))
        stack.enter_context(patch.object(setup.sysconfig, 'get_config_var', return_value=gil))
        return stack

    def test_supported_python_versions(self):
        for version in ((3,10), (3,11), (3,12), (3,13), (3,14)):
            with self.subTest(version=version), self.runtime(version=version):
                setup.validate_runtime()

    def test_unsupported_interpreters_rejected_before_install(self):
        for kwargs in ({'version':(3,9)}, {'version':(3,15)}, {'bits':4}, {'machine':'ARM64'},
                       {'implementation':'PyPy'}, {'gil':1}):
            with self.subTest(kwargs=kwargs), self.runtime(**kwargs):
                with self.assertRaises(setup.SetupError): setup.validate_runtime()

    def test_non_windows_rejected(self):
        with patch.object(setup.sys, 'platform', 'linux'):
            with self.assertRaises(setup.SetupError): setup.validate_runtime()

    def test_complete_checkout_required(self):
        with patch.object(setup, 'ROOT', self.root):
            setup.validate_app(self.app)
            with self.assertRaises(setup.SetupError): setup.validate_app(self.root / 'elsewhere')
            (self.app / 'assets/icon.ico').unlink()
            with self.assertRaises(setup.SetupError): setup.validate_app(self.app)

    def test_foreign_environment_is_not_removed(self):
        folder = self.app / '.venv-windows'
        folder.mkdir()
        keep = folder / 'unrelated.txt'
        keep.write_text('keep')
        with self.assertRaises(setup.SetupError): setup.prepare_environment(self.app, FakeLog())
        self.assertEqual(keep.read_text(), 'keep')

    def test_managed_environment_created_and_reused(self):
        log = FakeLog()
        python = setup.prepare_environment(self.app, log)
        self.assertTrue(python.is_file())
        self.assertEqual(len(log.calls), 1)
        reused = FakeLog()
        self.assertEqual(setup.prepare_environment(self.app, reused), python)
        self.assertEqual(len(reused.calls), 1)
        self.assertIn('Checking the existing', reused.calls[0][1])

    def test_corrupt_owned_environment_repaired(self):
        setup.prepare_environment(self.app, FakeLog())
        marker = self.app / '.venv-windows' / setup.MARKER
        data = json.loads(marker.read_text())
        data['version'] = 'old'
        marker.write_text(json.dumps(data))
        log = FakeLog()
        setup.prepare_environment(self.app, log)
        self.assertIn('Creating', log.calls[0][1])
        self.assertNotEqual(json.loads(marker.read_text())['version'], 'old')

    def test_broken_existing_interpreter_repaired(self):
        setup.prepare_environment(self.app, FakeLog())
        log = FakeLog(fail='Checking the existing')
        python = setup.prepare_environment(self.app, log)
        self.assertTrue(python.is_file())
        self.assertEqual(len(log.calls), 2)

    def test_each_failed_step_stops_before_build_success(self):
        steps = ('Updating', 'Installing', 'Checking installed', 'Checking desktop', 'Building')
        for step in steps:
            log = FakeLog(fail=step)
            stdout = io.StringIO()
            with self.subTest(step=step), patch.object(setup, 'prepare_environment', return_value=self.app/'python.exe'), contextlib.redirect_stdout(stdout):
                with self.assertRaises(setup.SetupError): setup.build(self.app, log)
            self.assertTrue(log.calls[-1][1].startswith(step))
            self.assertNotIn('Setup complete', stdout.getvalue())

    def test_build_uses_private_python_and_quoted_argument_lists(self):
        log = FakeLog()
        python = self.app / '.venv-windows/Scripts/python.exe'
        with patch.object(setup, 'prepare_environment', return_value=python), contextlib.redirect_stdout(io.StringIO()):
            setup.build(self.app, log)
        self.assertTrue(all(command[0] == python for command, _, _ in log.calls))
        install = next(cmd for cmd, _, _ in log.calls if '--only-binary=:all:' in cmd)
        self.assertIn(self.app / 'requirements.txt', install)
        build = log.calls[-1][0]
        self.assertEqual(build[build.index('--add-data')+1], str(self.app/'assets')+';assets')
        self.assertIn('PyQt6', build)
        self.assertEqual(build[-1], self.app / 'main.py')

    def test_update_build_targets_staging_without_removing_installed_executable(self):
        original = self.app / 'dist' / 'ClothShopBilling.exe'
        original.parent.mkdir(); original.write_bytes(b'MZ existing executable')
        staged = self.app / 'staged'
        with patch.object(setup, 'prepare_environment', return_value=self.app/'python.exe'), contextlib.redirect_stdout(io.StringIO()):
            setup.build(self.app, FakeLog(), dist_dir=staged)
        self.assertEqual(original.read_bytes(), b'MZ existing executable')
        self.assertTrue((staged/'ClothShopBilling.exe').read_bytes().startswith(b'MZ'))

    def test_missing_executable_does_not_report_success(self):
        stdout = io.StringIO()
        with patch.object(setup, 'prepare_environment', return_value=self.app/'python.exe'), contextlib.redirect_stdout(stdout):
            with self.assertRaises(setup.SetupError): setup.build(self.app, FakeLog(output=False))
        self.assertNotIn('Setup complete', stdout.getvalue())

    def test_skip_build_still_checks_desktop(self):
        log = FakeLog()
        with patch.object(setup, 'prepare_environment', return_value=self.app/'python.exe'), contextlib.redirect_stdout(io.StringIO()):
            setup.build(self.app, log, skip_build=True)
        self.assertTrue(log.calls[-1][1].startswith('Checking desktop'))
        self.assertFalse(any('PyInstaller' in command for command, _, _ in log.calls))

    def test_output_and_tracebacks_go_to_log(self):
        target = self.app / 'setup.log'
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            log = setup.SetupLog(target)
            try:
                with self.assertRaises(setup.SetupError):
                    log.run([sys.executable, '-c', "print('first real error'); raise RuntimeError('failed')"], 'Test child', self.app)
            finally:
                log.close()
        self.assertIn('first real error', target.read_text())
        self.assertIn('RuntimeError', target.read_text())
        self.assertNotIn('Traceback', stdout.getvalue())

    def test_main_returns_failure_and_prints_log_path(self):
        stderr, stdout = io.StringIO(), io.StringIO()
        with patch.object(setup, 'ROOT', self.root), patch.object(setup, 'validate_runtime'), patch.object(setup, 'build', side_effect=setup.SetupError('missing wheel')), contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
            self.assertEqual(setup.main(['--app-dir',str(self.app)]), 1)
        self.assertIn(str(self.app/'setup.log'), stderr.getvalue())
        self.assertIn('missing wheel', (self.app/'setup.log').read_text())
        self.assertNotIn('Setup complete', stdout.getvalue())

    def test_python_probe_is_quiet_on_failure(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(setup, 'validate_runtime', side_effect=setup.SetupError('unsupported')), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(setup.main(['--check-python']), 1)
        self.assertEqual(stdout.getvalue()+stderr.getvalue(), '')

    def test_conflicting_global_environment_is_not_inherited(self):
        with patch.dict(os.environ, {'PYTHONPATH':'wrong','PYTHONHOME':'wrong','QT_PLUGIN_PATH':'wrong','QT_QPA_PLATFORM_PLUGIN_PATH':'wrong'}):
            clean = setup.clean_environment()
        for key in ('PYTHONPATH','PYTHONHOME','QT_PLUGIN_PATH','QT_QPA_PLATFORM_PLUGIN_PATH'):
            self.assertNotIn(key, clean)
        self.assertEqual(clean['PYTHONNOUSERSITE'], '1')
        self.assertEqual(clean['QT_API'], 'pyside6')


if __name__ == '__main__':
    unittest.main()
