"""OS printing transport. Linux can submit PDFs through CUPS without Qt's plugin.

Never retry a submission automatically: a timeout can still mean paper printed.
Windows keeps Qt's native printer driver; saved PDFs never enter a print queue.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from PySide6.QtPrintSupport import QPrinterInfo


class PrintError(ValueError):
    pass


def printing_backend():
    return 'cups' if sys.platform.startswith('linux') and shutil.which('lp') and shutil.which('lpstat') else 'qt'


def printer_setup_help():
    if sys.platform.startswith('linux'):
        return ('Linux needs a running CUPS service and a configured printer queue with a compatible driver. '
                'On Arch Linux:\n'
                'sudo pacman -S cups cups-filters\n'
                'sudo systemctl enable --now cups.service\n'
                'Add your printer in Print Settings or http://localhost:631, then print a system test page. '
                'For a Citizen label printer, use a driver supporting your model and custom media; a raw queue '
                'cannot print PDF documents.\n'
                'Check: lpstat -r\nCheck printers: lpstat -p -a\nCheck jobs: lpstat -o\n'
                'After setup, click Refresh printers. You can save a PDF without a printer.')
    return ('Install the manufacturer printer driver in Windows Settings → Bluetooth & devices → '
            'Printers & scanners. Print a Windows test page, then click Refresh printers. '
            'Check the Windows print queue if a submitted job does not reach paper. '
            'The Windows executable must be rebuilt after pulling an update. You can save a PDF without a printer.')


def printer_setup_hint():
    if sys.platform.startswith('linux'):
        return 'No printer available. Install/start CUPS and add the printer in Print Settings. See Setup / help for Arch commands.'
    return 'No printer available. Install the Windows printer driver, then refresh. See Setup / help.'


def _cups(command, timeout=10, input_bytes=None):
    env=os.environ.copy();env.update(LC_ALL='C',LANG='C')
    try:
        if input_bytes is None:
            result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='replace',
                                  env=env,timeout=timeout,check=False)
        else:
            result=subprocess.run(command,input=input_bytes,capture_output=True,env=env,timeout=timeout,check=False)
            result.stdout=result.stdout.decode('utf-8','replace');result.stderr=result.stderr.decode('utf-8','replace')
    except subprocess.TimeoutExpired as exc:
        raise PrintError('CUPS did not respond. Check the printer queue before retrying; the job may already be submitted.') from exc
    except OSError as exc:
        raise PrintError('Could not run CUPS printing tools.\n'+printer_setup_help()) from exc
    if result.returncode:
        detail=(result.stderr or result.stdout).strip()[:1000]
        raise PrintError('CUPS: '+(detail or 'command failed')+'\n'+printer_setup_help())
    return result.stdout


def _queue_name(queue):
    if not isinstance(queue,str) or not queue or len(queue)>127 or queue.startswith('-') or any(c.isspace() or c in '/#?' or ord(c)<32 for c in queue):
        raise PrintError('Choose a valid installed CUPS printer queue.')
    return queue


def available_printers():
    if printing_backend()=='cups':
        # -a lists configured queues, including paused/rejecting ones so users can see and repair them.
        output=_cups(['lpstat','-a'])
        return sorted(set(line.split()[0] for line in output.splitlines() if line.strip()))
    return QPrinterInfo.availablePrinterNames()


def check_cups_queue(queue):
    queue=_queue_name(queue)
    state=_cups(['lpstat','-p',queue])
    if re.search(r'^printer\s+\S+\s+disabled\b',state,re.MULTILINE):
        raise PrintError('The selected printer is paused/disabled. Resume it in Print Settings and print a system test page.')
    accepting=_cups(['lpstat','-a',queue])
    if 'not accepting requests' in accepting:
        raise PrintError('The selected printer is rejecting jobs. Enable accepting jobs in Print Settings before printing.')


def queue_status(queue):
    if printing_backend()!='cups':
        return 'Open the system printer queue and check for paused jobs, paper/connection errors and driver errors.\n'+printer_setup_help()
    queue=_queue_name(queue)
    state=_cups(['lpstat','-l','-p',queue])
    jobs=_cups(['lpstat','-W','not-completed','-l','-o',queue])
    return state+'\nPending jobs:\n'+(jobs.strip() or 'None')+'\nIf no paper comes out, print a system test page and check the CUPS filter/driver log. Fixed Citizen labels use raw ZPL; PDF receipts require a PDF-capable driver.'


def submit_pdf(path,queue,width_mm,height_mm,dpi=300,copies=1,title='Cloth Shop'):
    """Return the acknowledged CUPS job id, once. Never invoke a shell or auto retry."""
    queue=_queue_name(queue)
    if isinstance(copies,bool) or not isinstance(copies,int) or not 1<=copies<=99:
        raise PrintError('Choose between 1 and 99 printer copies.')
    for value in (width_mm,height_mm):
        if not isinstance(value,(int,float)) or not 1<=value<=1000:
            raise PrintError('Invalid print media dimensions.')
    if dpi not in (203,300,600):raise PrintError('Unsupported print resolution.')
    path=Path(path).resolve()
    with path.open('rb') as stream:
        if stream.read(5)!=b'%PDF-':raise PrintError('The printable PDF was not created.')
    check_cups_queue(queue)
    output=_cups(['lp','-d',queue,'-n',str(copies),'-t',title,
                  '-o',f'media=Custom.{width_mm:g}x{height_mm:g}mm',
                  '-o','print-scaling=none','-o','fit-to-page=false','-o','scaling=100',
                  '-o','orientation-requested=3','-o','sides=one-sided','-o','job-sheets=none',
                  '-o',f'printer-resolution={dpi}dpi',str(path)],timeout=30)
    match=re.search(r'request id is (\S+-\d+)',output)
    if not match:
        raise PrintError('CUPS did not return a job id. Check lpstat -o and the printer before retrying; the job may already exist.')
    return match.group(1)


def submit_raw(queue,zpl):
    """Send one ZPL document; ^PQ is the only label count (no spooler copies)."""
    if not isinstance(zpl,bytes) or not zpl.startswith(b'^XA\n') or not zpl.rstrip().endswith(b'^XZ') or len(zpl)>100000:
        raise PrintError('Invalid Citizen print document.')
    if sys.platform=='win32':
        from windows_raw import submit_windows_raw
        return 'Windows job '+str(submit_windows_raw(queue,zpl))
    if not sys.platform.startswith('linux') or printing_backend()!='cups':
        raise PrintError('Raw Citizen printing needs Linux CUPS tools or a Windows printer queue. Use Install / setup.')
    queue=_queue_name(queue);check_cups_queue(queue)
    output=_cups(['lp','-d',queue,'-o','raw','-n','1','-o','job-sheets=none','-t','Cloth Shop Citizen Labels'],timeout=30,input_bytes=zpl)
    match=re.search(r'request id is (\S+-\d+)',output)
    if not match:raise PrintError('No CUPS job id returned. Check the queue before retrying; the job may already be submitted.')
    return 'CUPS job '+match.group(1)
