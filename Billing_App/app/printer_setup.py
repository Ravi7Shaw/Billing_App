"""Explicit, user-started Citizen printer setup. CLI uses only the standard library."""
import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from urllib.parse import unquote

QUEUE='Citizen_CL_S621II'


def run(command):
    print('Running: '+shlex.join(command),flush=True)
    result=subprocess.run(command,check=False)
    if result.returncode:raise RuntimeError('Setup command failed. Check the output above, repair it and run setup again.')


def setup_linux():
    if not sys.platform.startswith('linux'):raise RuntimeError('This installer is for Linux. Use Windows Add Printer on Windows.')
    if os.geteuid()!=0:raise RuntimeError('Run this setup with administrator privileges (sudo or the app Install / setup button).')
    if not all(shutil.which(name) for name in ('lp','lpstat','lpadmin','lpinfo')):
        if shutil.which('pacman'):command=['pacman','-S','--needed','--noconfirm','cups','cups-filters']
        elif shutil.which('apt-get'):command=['apt-get','install','-y','cups','cups-client','cups-filters']
        elif shutil.which('dnf'):command=['dnf','install','-y','cups','cups-client','cups-filters']
        else:raise RuntimeError('Install CUPS with your distribution package manager, then rerun setup.')
        run(command)
    if shutil.which('systemctl'):run(['systemctl','enable','--now','cups.service'])
    elif subprocess.run(['lpstat','-r'],capture_output=True).returncode:
        raise RuntimeError('Start the CUPS service with your system service manager, then rerun setup.')
    result=subprocess.run(['lpstat','-a'],capture_output=True,text=True,check=False)
    if result.returncode:raise RuntimeError('CUPS is not responding. Start it and rerun setup.')
    for line in result.stdout.splitlines():
        name=line.split()[0] if line.split() else ''
        if name==QUEUE or 'citizen' in name.lower():
            print('Existing printer queue kept: '+name+'\nReturn to the app and click Refresh printers.',flush=True);return
    result=subprocess.run(['lpinfo','-v'],capture_output=True,text=True,check=False)
    if result.returncode:raise RuntimeError('Could not discover the printer. Connect and power on the Citizen, then retry.')
    candidates=[]
    for line in result.stdout.splitlines():
        parts=line.split(None,1)
        if len(parts)!=2:continue
        uri=parts[1].strip();decoded=unquote(uri).lower()
        if uri.startswith('usb://') and 'citizen' in decoded and '621' in decoded:candidates.append(uri)
    candidates=sorted(set(candidates))
    if len(candidates)!=1:
        raise RuntimeError('CUPS tools are ready. Connect one Citizen CL-S621II by USB and retry, or add its queue in Print Settings for network/other connections. No printer port was guessed.')
    run(['lpadmin','-p',QUEUE,'-E','-v',candidates[0],'-m','raw'])
    print('Citizen raw ZPL queue ready: '+QUEUE+'\nUse ZPL/Zebra emulation on the printer. Return to the app and refresh printers.',flush=True)


def open_setup(parent):
    from PySide6.QtCore import QProcess
    from PySide6.QtWidgets import QDialog,QVBoxLayout,QPlainTextEdit,QDialogButtonBox,QMessageBox
    from window_utils import fit_window
    if sys.platform=='win32':
        try:subprocess.Popen(['rundll32.exe','printui.dll,PrintUIEntry','/il'])
        except OSError as exc:QMessageBox.warning(parent,'Printer setup',str(exc))
        else:QMessageBox.information(parent,'Windows printer setup','Use Add Printer to install the Citizen driver/queue on its USB or network port. Windows can find the matching driver, or use the manufacturer driver installer. The app then sends RAW ZPL through that queue. After setup, refresh printers. Keep the printer in ZPL/Zebra emulation.')
        return
    script=Path(__file__).resolve()
    if not shutil.which('pkexec'):
        command=shlex.join(['sudo',sys.executable,str(script),'--install-citizen'])
        QMessageBox.information(parent,'Install printing support','Run this command in your terminal; it installs missing CUPS tools, starts CUPS and configures a connected Citizen USB printer:\n\n'+command)
        return
    class SetupDialog(QDialog):
        def reject(self):
            if self.process.state()==QProcess.NotRunning:super().reject()
    dialog=SetupDialog(parent);dialog.setWindowTitle('Install Citizen printing support')
    layout=QVBoxLayout(dialog);output=QPlainTextEdit();output.setReadOnly(True);layout.addWidget(output)
    output.setPlainText('Your OS may ask for administrator authentication. Setup installs missing CUPS packages and keeps an existing Citizen queue.\n')
    buttons=QDialogButtonBox(QDialogButtonBox.Close);layout.addWidget(buttons)
    close=buttons.button(QDialogButtonBox.Close);close.setEnabled(False);buttons.rejected.connect(dialog.reject)
    process=dialog.process=QProcess(dialog);process.setProcessChannelMode(QProcess.MergedChannels)
    process.readyReadStandardOutput.connect(lambda:output.appendPlainText(bytes(process.readAllStandardOutput()).decode('utf-8','replace')))
    process.finished.connect(lambda code,*_: (output.appendPlainText('Setup completed. Refresh printers.' if code==0 else 'Setup did not complete. Read the messages above.'),close.setEnabled(True)))
    process.errorOccurred.connect(lambda *_:(output.appendPlainText(process.errorString()),close.setEnabled(True)))
    process.start('pkexec',[sys.executable,str(script),'--install-citizen'])
    fit_window(dialog,640,420);dialog.exec()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Install missing Linux CUPS tools and set up a Citizen CL-S621II raw ZPL USB queue.')
    parser.add_argument('--install-citizen',action='store_true')
    args=parser.parse_args()
    if args.install_citizen:
        try:setup_linux()
        except (OSError,RuntimeError) as exc:
            print(str(exc),file=sys.stderr,flush=True);sys.exit(1)
