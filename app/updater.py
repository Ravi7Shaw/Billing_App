"""Checkout update checks and a detached Windows rebuild helper (standard library only)."""
import argparse
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time


class UpdateError(RuntimeError):
    pass


def child_environment():
    env = os.environ.copy()
    for key in list(env):
        if key.startswith('_PYI') or key in ('PYTHONHOME', 'PYTHONPATH', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH'):
            env.pop(key, None)
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    env['GIT_TERMINAL_PROMPT'] = '0'
    return env


@contextmanager
def external_process():
    # PyInstaller's Windows DLL directory is inherited by child processes.
    # Let system Git/Python load their own DLLs, then restore our app's path.
    reset = sys.platform == 'win32' and getattr(sys, 'frozen', False)
    if reset:
        import ctypes
        set_directory = ctypes.windll.kernel32.SetDllDirectoryW
        set_directory.argtypes = [ctypes.c_wchar_p]
        set_directory(None)
    try:
        yield
    finally:
        if reset:
            set_directory(getattr(sys, '_MEIPASS', None))


def git(root, *args):
    try:
        with external_process():
            result = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                                text=True, timeout=90, env=child_environment(),
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError('Git could not run. Install Git for Windows and check the internet connection.') from exc
    if result.returncode:
        raise UpdateError('Git update check failed: ' + result.stderr.strip())
    return result.stdout.strip()


def checkout_location():
    location = Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve()
    app_dir = location.parent.parent if getattr(sys, 'frozen', False) else location.parent
    for root in location.parents:
        if (root / '.git').exists() and (root / 'tools/windows_setup.py').is_file():
            if app_dir not in (root/'app', root/'Billing_App/app'):
                break
            return root, app_dir
    raise UpdateError('Run the app from app/dist or Billing_App/app/dist inside the complete Git checkout. '
                      'A copied executable or ZIP download cannot pull updates; clone the repository and run setup.bat once.')


def check_update(root, app_dir):
    root, app_dir = Path(root).resolve(), Path(app_dir).resolve()
    if app_dir not in (root/'app', root/'Billing_App/app'):
        raise UpdateError('The application folder does not belong to this checkout.')
    if git(root, 'status', '--porcelain'):
        raise UpdateError('This checkout has local changes. Commit or move them before updating.')
    branch = git(root, 'symbolic-ref', '--quiet', '--short', 'HEAD')
    remote = git(root, 'config', f'branch.{branch}.remote')
    remote_ref = git(root, 'config', f'branch.{branch}.merge')
    if remote == '.' or not remote_ref.startswith('refs/heads/'):
        raise UpdateError('The current branch needs a remote tracking branch before it can update.')
    head = git(root, 'rev-parse', 'HEAD')
    git(root, 'fetch', '--no-tags', remote, remote_ref)
    target = git(root, 'rev-parse', 'FETCH_HEAD')
    if git(root, 'merge-base', head, target) != head:
        raise UpdateError('Local and remote history differ. Resolve the branch before updating.')
    count = int(git(root, 'rev-list', '--count', head+'..'+target))
    return dict(root=str(root), app_dir=str(app_dir), branch=branch, head=head, target=target, count=count)


def find_python(root):
    for command in ([['py', '-'+v] for v in ('3.13', '3.12', '3.14', '3.11', '3.10')] + [['python']]):
        if not shutil.which(command[0]):
            continue
        try:
            with external_process():
                result = subprocess.run(command + [str(Path(root)/'tools/windows_setup.py'), '--check-python'],
                capture_output=True, timeout=20, env=child_environment(),
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode == 0:
                with external_process():
                    base = subprocess.run(command + ['-I', '-c', 'import sys; print(sys._base_executable)'],
                        capture_output=True, text=True, timeout=20, env=child_environment(),
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if base.returncode == 0 and base.stdout.strip():
                    # Rebuilding must not run inside the environment it may repair.
                    return [base.stdout.strip()]
        except (OSError, subprocess.TimeoutExpired):
            continue
    raise UpdateError('Install standard 64-bit Python 3.10–3.14 with the Python launcher, then try Update again.')


def start_update(plan, python):
    folder = Path(tempfile.mkdtemp(prefix='billing-update-'))
    try:
        # Frozen modules live in PyInstaller's archive; use the checked source file.
        helper = folder/'updater.py'
        shutil.copy2(Path(plan['app_dir'])/'updater.py', helper)
        plan_file = folder/'plan.json'
        plan_file.write_text(json.dumps(dict(plan, parent_pid=os.getpid())), encoding='utf-8')
        with external_process():
            subprocess.Popen(python + [str(helper), '--plan', str(plan_file)], cwd=plan['root'],
                         env=child_environment(), creationflags=subprocess.CREATE_NEW_CONSOLE)
    except Exception:
        shutil.rmtree(folder)
        raise


def wait_for_exit(pid):
    # Wait for the running app before replacing its executable (including PyInstaller).
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x100000, False, int(pid))
    if not handle:
        if ctypes.get_last_error() == 87:  # Already exited.
            return
        raise UpdateError('Could not wait for the billing app to close.')
    try:
        if kernel.WaitForSingleObject(handle, 120000) != 0:
            raise UpdateError('The billing app is still open. Close it and try Update again.')
    finally:
        kernel.CloseHandle(handle)


def apply_update(plan, builder):
    """Fast-forward the checked branch, build to staging, then replace the executable."""
    root, app_dir = Path(plan['root']).resolve(), Path(plan['app_dir']).resolve()
    if app_dir not in (root/'app', root/'Billing_App/app'):
        raise UpdateError('Invalid application folder.')
    for key in ('head', 'target'):
        if not re.fullmatch(r'[0-9a-f]{40,64}', plan[key]):
            raise UpdateError('Invalid update revision.')
    if git(root, 'status', '--porcelain') or git(root, 'rev-parse', 'HEAD') != plan['head']:
        raise UpdateError('The checkout changed after checking updates. Check again.')
    if git(root, 'symbolic-ref', '--short', 'HEAD') != plan['branch']:
        raise UpdateError('The selected branch changed. Check again.')
    git(root, 'merge', '--ff-only', plan['target'])
    stage_parent = app_dir/'build'
    stage_parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='update-', dir=stage_parent) as stage:
        stage = Path(stage)
        builder(root, app_dir, stage)
        built = stage/'ClothShopBilling.exe'
        with built.open('rb') as stream:
            if stream.read(2) != b'MZ':
                raise UpdateError('Build did not produce a Windows executable. The previous app is unchanged.')
        destination = app_dir/'dist/ClothShopBilling.exe'
        destination.parent.mkdir(exist_ok=True)
        if destination.exists():
            shutil.copy2(destination, destination.with_name('ClothShopBilling.previous.exe'))
        # The bootloader or virus scanner may release its file handle just after exit.
        for attempt in range(30):
            try:
                os.replace(built, destination)
                break
            except PermissionError:
                if attempt == 29:
                    raise UpdateError('Windows is holding the executable open. The previous app is unchanged. Close other copies and retry.')
                time.sleep(1)
        return destination


def build_windows(root, app_dir, stage):
    spec = importlib.util.spec_from_file_location('billing_windows_setup', root/'tools/windows_setup.py')
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    setup.validate_runtime()
    log = setup.SetupLog(app_dir/'setup.log')
    try:
        setup.build(app_dir, log, dist_dir=stage)
    finally:
        log.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', required=True, type=Path)
    args = parser.parse_args()
    try:
        plan = json.loads(args.plan.read_text(encoding='utf-8'))
        print('Waiting for billing to close...', flush=True)
        wait_for_exit(plan['parent_pid'])
        executable = apply_update(plan, build_windows)
        print('Update complete. Reopening billing...', flush=True)
        subprocess.Popen([str(executable)], cwd=executable.parent, env=child_environment())
    except Exception as exc:
        print('Update failed: '+str(exc), flush=True)
        print('The previous executable has been kept. Build details are in the app folder’s setup.log.', flush=True)
        input('Press Enter to close this window.')
        return 1
    finally:
        args.plan.unlink(missing_ok=True)
        if Path(__file__).parent == args.plan.parent and args.plan.parent.name.startswith('billing-update-'):
            shutil.rmtree(args.plan.parent, ignore_errors=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
