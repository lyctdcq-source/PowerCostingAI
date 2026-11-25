"""Per-user Windows installer; includes runtime and creates desktop/start-menu shortcuts."""
import base64
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox
import zipfile

HERE = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
DEST = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Programs' / 'PowerCostAgent'
APP_NAME = '电力造价智能体平台'
PUBLISHER = '云南中合数维科技有限责任公司'
VERSION = (HERE / 'VERSION').read_text(encoding='utf-8').strip() if (HERE / 'VERSION').exists() else '0.0.0'
UNINSTALL_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\PowerCostAgent'


def extract(destination):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(HERE / 'payload.zip') as z:
        for item in z.infolist():
            target = (destination / item.filename).resolve()
            if not target.is_relative_to(destination):
                raise ValueError('安装包路径校验失败')
        z.extractall(destination)


def powershell(script):
    # 以 -EncodedCommand 传入整段脚本：经标准输入传入时，多行语句块不会执行且仍返回成功
    encoded = base64.b64encode(script.encode('utf-16le')).decode()
    proc = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded],
                          text=True, encoding='utf-8', errors='replace', capture_output=True, creationflags=0x08000000)
    if proc.returncode:
        raise RuntimeError('快捷方式写入失败：' + (proc.stderr or proc.stdout).strip()[:200])
    return proc.stdout


def quoted(text):
    return "'" + str(text).replace("'", "''") + "'"


def create_shortcuts():
    exe = DEST / 'PowerCostAgent.exe'
    output = powershell(f"""$ErrorActionPreference='Stop'
try{{[Console]::OutputEncoding=[Text.Encoding]::UTF8}}catch{{}}
$w=New-Object -ComObject WScript.Shell
foreach($base in @([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('Programs'))){{
 $path=Join-Path $base ({quoted(APP_NAME)}+'.lnk')
 $link=$w.CreateShortcut($path)
 $link.TargetPath={quoted(exe)}
 $link.WorkingDirectory={quoted(DEST)}
 $link.IconLocation={quoted(str(exe) + ',0')}
 $link.Description={quoted(APP_NAME + ' ' + VERSION)}
 $link.Save()
 Write-Output $path
}}
""")
    links = [Path(line.strip()) for line in output.splitlines() if line.strip().lower().endswith('.lnk')]
    if len(links) != 2 or not all(link.exists() for link in links):
        raise RuntimeError('桌面或开始菜单快捷方式未能创建')
    return links


def register():
    import winreg
    size = sum(p.stat().st_size for p in DEST.rglob('*') if p.is_file()) // 1024
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        values = {'DisplayName': APP_NAME, 'DisplayVersion': VERSION, 'Publisher': PUBLISHER, 'InstallLocation': str(DEST),
                  'DisplayIcon': str(DEST / 'PowerCostAgent.exe') + ',0', 'InstallDate': time.strftime('%Y%m%d'),
                  'UninstallString': '"' + str(DEST / 'Uninstall.exe') + '" --uninstall'}
        for name, val in values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, val)
        for name, val in {'EstimatedSize': size, 'NoModify': 1, 'NoRepair': 1}.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, val)


def setup():
    extract(DEST)
    shutil.copy2(sys.executable, DEST / 'Uninstall.exe')
    register()
    create_shortcuts()


def uninstall(root):
    if not messagebox.askyesno('卸载电力造价智能体平台', '将移除程序与快捷方式，保留本地项目数据和检查点。请先退出运行中的工作台。继续卸载？'):
        root.destroy()
        return
    # Only this exact per-user application directory may be removed.
    expected = Path(os.environ['LOCALAPPDATA']).resolve() / 'Programs' / 'PowerCostAgent'
    if DEST.resolve() != expected.resolve() or DEST.name != 'PowerCostAgent':
        raise ValueError('卸载目录校验失败')
    folder = str(DEST.resolve()).replace("'", "''")
    script = rf"""Start-Sleep -Seconds 3
$target='{folder}'
$expected=Join-Path $env:LOCALAPPDATA 'Programs\PowerCostAgent'
if([IO.Path]::GetFullPath($target) -ne [IO.Path]::GetFullPath($expected)){{exit 1}}
$name=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{base64.b64encode(APP_NAME.encode()).decode()}'))
foreach($base in @([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('Programs'))){{
 $shortcut=Join-Path $base ($name+'.lnk')
 if(Test-Path -LiteralPath $shortcut){{Remove-Item -LiteralPath $shortcut -Force}}
}}
Remove-Item -LiteralPath $target -Recurse -Force
Remove-Item -LiteralPath 'HKCU:\{UNINSTALL_KEY}' -Force -ErrorAction SilentlyContinue
"""
    encoded = base64.b64encode(script.encode('utf-16le')).decode()
    subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded], creationflags=0x08000000)
    root.destroy()


def main():
    if '--extract-to' in sys.argv:
        extract(sys.argv[sys.argv.index('--extract-to') + 1])
        return
    root = tk.Tk()
    root.title(f'{APP_NAME} {VERSION} · 安装')
    root.geometry('560x370')
    root.configure(bg='#f4f7f0')
    root.resizable(False, False)
    if '--uninstall' in sys.argv:
        root.withdraw()
        uninstall(root)
        return
    tk.Label(root, text=APP_NAME, bg='#f4f7f0', fg='#285f43', font=('Microsoft YaHei', 20)).pack(anchor='w', padx=32, pady=(28, 6))
    tk.Label(root, text=f'版本 {VERSION} · {PUBLISHER}', bg='#f4f7f0', fg='#66795e', font=('Microsoft YaHei', 10)).pack(anchor='w', padx=32)
    tk.Label(root, text='Windows 桌面客户端 · 无需 Python · 无需显卡', bg='#f4f7f0', fg='#66795e', font=('Microsoft YaHei', 11)).pack(anchor='w', padx=32, pady=(10, 0))
    tk.Label(root, text='安装后在桌面和开始菜单“所有应用”中创建快捷方式，需要 WebView2 Runtime。\n工程文件在本机处理；在线模型按配置调用。\n\n安装位置：'+str(DEST), justify='left', wraplength=500, bg='#f4f7f0', fg='#76816f', font=('Microsoft YaHei', 10)).pack(anchor='w', padx=32, pady=16)
    status = tk.Label(root, text='安装仅作用于当前用户，无需管理员权限。', bg='#f4f7f0', fg='#76816f', font=('Microsoft YaHei', 9), justify='left', wraplength=500)
    status.pack(anchor='w', padx=32)
    errors = []
    done = threading.Event()
    def run():
        try:
            setup()
        except Exception as exc:
            errors.append(str(exc))
        done.set()
    def poll():
        if not done.is_set():
            root.after(150, poll)
            return
        if errors:
            status.config(text='安装未完成：'+errors[0])
            button.config(text='重试安装', state='normal')
        else:
            status.config(text='安装完成。可从桌面图标或开始菜单“所有应用”中的“电力造价智能体平台”启动。')
            button.config(text='启动工作台', state='normal', command=lambda: (subprocess.Popen([str(DEST / 'PowerCostAgent.exe')], cwd=DEST), root.destroy()))
    def start():
        done.clear()
        errors.clear()
        button.config(text='正在安装…', state='disabled')
        status.config(text='正在安装程序与本地运行环境…')
        threading.Thread(target=run, daemon=True).start()
        root.after(150, poll)
    button = tk.Button(root, text='安装到本机', command=start, bg='#327451', fg='white', font=('Microsoft YaHei', 11), relief='flat', padx=18, pady=7)
    button.pack(anchor='e', padx=32, pady=14)
    root.mainloop()


if __name__ == '__main__':
    main()
