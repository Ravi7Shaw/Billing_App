"""Prepare and build either desktop copy on Windows without global installs."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import sysconfig
import traceback

ROOT = Path(__file__).resolve().parents[1]
MARKER = '.billing-setup.json'


class SetupError(Exception):
    pass


def validate_runtime():
    if sys.platform != 'win32':
        raise SetupError('This build script requires Windows 10 or 11 (x64).')
    if (platform.python_implementation() != 'CPython'
            or not (3, 10) <= sys.version_info[:2] < (3, 15)
            or struct.calcsize('P') != 8
            or platform.machine().lower() not in ('amd64', 'x86_64')
            or sysconfig.get_config_var('Py_GIL_DISABLED')):
        raise SetupError('Install standard 64-bit CPython 3.10 through 3.14 from https://www.python.org/downloads/windows/.')
    if sys.getwindowsversion().major < 10:
        raise SetupError('The desktop dependencies require Windows 10 or 11.')


def clean_environment():
    env = os.environ.copy()
    for name in ('PYTHONHOME', 'PYTHONPATH', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH'):
        env.pop(name, None)
    env.update(PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1',
               PIP_DISABLE_PIP_VERSION_CHECK='1', PIP_NO_INPUT='1', QT_API='pyside6')
    return env


class SetupLog:
    def __init__(self, path):
        self.path = path
        self.stream = path.open('w', encoding='utf-8')
        self.env = clean_environment()

    def close(self):
        self.stream.close()

    def run(self, command, description, cwd):
        print(description + '...')
        self.stream.write('\n' + description + '\n' + subprocess.list2cmdline([str(x) for x in command]) + '\n')
        self.stream.flush()
        try:
            result = subprocess.run([str(x) for x in command], cwd=str(cwd), env=self.env,
                                    stdout=self.stream, stderr=subprocess.STDOUT, check=False)
        except OSError as exc:
            raise SetupError(description + ' could not start: ' + str(exc)) from exc
        if result.returncode:
            raise SetupError(description + ' failed (exit code ' + str(result.returncode) + ').')

    def record_exception(self):
        traceback.print_exc(file=self.stream)
        self.stream.flush()


def validate_app(app_dir):
    if app_dir not in (ROOT / 'app', ROOT / 'Billing_App' / 'app'):
        raise SetupError('Select app/ or Billing_App/app/ inside this complete repository checkout.')
    for relative in ('main.py', 'requirements.txt', 'assets/icon.ico'):
        if not (app_dir / relative).is_file():
            raise SetupError('Missing ' + str(app_dir / relative) + '. Download or pull the complete repository again.')


def prepare_environment(app_dir, log):
    env_dir = app_dir / '.venv-windows'
    marker = env_dir / MARKER
    python = env_dir / 'Scripts' / 'python.exe'
    expected = {'owner': 'cloth-shop-billing', 'python': str(Path(sys.executable).resolve()),
                'version': platform.python_version()}
    if env_dir.is_symlink():
        raise SetupError('The setup environment cannot be a symbolic link: ' + str(env_dir))
    if env_dir.exists():
        try:
            previous = json.loads(marker.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise SetupError('An unrecognized folder exists at ' + str(env_dir) + '. Rename that folder and run setup again.')
        if not isinstance(previous, dict) or previous.get('owner') != expected['owner']:
            raise SetupError('The environment is not owned by this setup: ' + str(env_dir))
        if previous == expected and python.is_file():
            try:
                log.run([python, '-I', '-c', 'import pip, sys; assert sys.prefix != sys.base_prefix'],
                        'Checking the existing app environment', app_dir)
                return python
            except SetupError:
                pass
        try:
            shutil.rmtree(env_dir)
        except OSError as exc:
            raise SetupError('Close the app and terminals using ' + str(env_dir) + ', then retry.') from exc
    env_dir.mkdir()
    marker.write_text(json.dumps(expected), encoding='utf-8')
    log.run([sys.executable, '-I', '-m', 'venv', env_dir], 'Creating the app environment', app_dir)
    if not python.is_file():
        raise SetupError('Python did not create the app environment correctly.')
    return python


def build(app_dir, log, skip_build=False, dist_dir=None):
    dist_dir = Path(dist_dir) if dist_dir is not None else app_dir / 'dist'
    python = prepare_environment(app_dir, log)
    log.run([python, '-I', '-m', 'pip', 'install', '--upgrade', 'pip'],
            'Updating the app installer', app_dir)
    log.run([python, '-I', '-m', 'pip', 'install', '--only-binary=:all:',
             '-r', app_dir / 'requirements.txt', '-r', ROOT / 'tools' / 'requirements-windows.txt'],
            'Installing compatible Windows packages', app_dir)
    log.run([python, '-I', '-m', 'pip', 'check'], 'Checking installed dependencies', app_dir)
    log.run([python, '-I', ROOT / 'tools' / 'smoke_desktop.py', '--app-dir', app_dir],
            'Checking desktop startup, receipts and database access', app_dir)
    if skip_build:
        print('Setup checks passed. Executable build was skipped.')
        print('Run the app with: "' + str(python) + '" "' + str(app_dir / 'main.py') + '"')
        return
    log.run([python, '-I', '-m', 'PyInstaller', '--noconfirm', '--clean', '--log-level=WARN',
             '--noconsole', '--onefile', '--name', 'ClothShopBilling',
             '--icon', app_dir / 'assets' / 'icon.ico',
             '--add-data', str(app_dir / 'assets') + ';assets',
             '--distpath', dist_dir, '--workpath', app_dir / 'build',
             '--specpath', app_dir / 'build',
             '--exclude-module', 'PyQt5', '--exclude-module', 'PyQt6',
             '--exclude-module', 'PySide2', app_dir / 'main.py'],
            'Building ClothShopBilling.exe (close any running copy first)', app_dir)
    executable = dist_dir / 'ClothShopBilling.exe'
    if not executable.is_file():
        raise SetupError('The build finished without creating ' + str(executable))
    with executable.open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise SetupError('The build output is not a Windows executable.')
    print('\nSetup complete. Open: ' + str(executable))
    print('Your shop database remains in your user profile under .cloth_shop_billing.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-python', action='store_true')
    parser.add_argument('--app-dir', type=Path, default=ROOT / 'Billing_App' / 'app')
    parser.add_argument('--skip-build', action='store_true', help='Install/check the app without packaging an executable.')
    args = parser.parse_args(argv)
    log = None
    try:
        validate_runtime()
        if args.check_python:
            return 0
        app_dir = args.app_dir.resolve()
        validate_app(app_dir)
        log = SetupLog(app_dir / 'setup.log')
        print('Using Python ' + platform.python_version() + ' (' + sys.executable + ')')
        print('Detailed output: ' + str(log.path))
        build(app_dir, log, args.skip_build)
        return 0
    except (SetupError, OSError) as exc:
        if args.check_python:
            return 1
        print('\nSETUP FAILED: ' + str(exc), file=sys.stderr)
        if log:
            log.record_exception()
            print('Full error details: ' + str(log.path), file=sys.stderr)
        print('For download errors, check your internet/proxy settings. For build errors, close the app and retry.',
              file=sys.stderr)
        return 1
    except Exception:
        if log:
            log.record_exception()
            print('Setup failed unexpectedly. Share this log: ' + str(log.path), file=sys.stderr)
        else:
            traceback.print_exc()
        return 1
    finally:
        if log:
            log.close()


if __name__ == '__main__':
    sys.exit(main())
