"""安装包构建：项目 Python 模块经 Nuitka 编译为本机代码（.pyd），技能模板与匹配引擎源码加密为 .pye，
PyInstaller 组装客户端与安装程序，并写入安装目录完整性清单。安装包中不含项目 .py 源码或字节码。

用法：.venv\\Scripts\\python build_secure.py [--skip-smoke]
Nuitka 与 MinGW 不支持中文路径，编译在系统临时目录（纯 ASCII 路径）中进行。"""
import ast
import concurrent.futures
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import protected  # noqa: E402

TEMP = Path(tempfile.gettempdir())
TOOLS = Path(os.environ.get('PCA_BUILD_TOOLS', TEMP / 'pca-build-tools'))
WORK = TEMP / 'pca-secure-build'
NUITKA = 'nuitka==2.7.*'
APP_ROOTS = ['desktop.py', 'LLM_service.py']
VERSION = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
PUBLISHER = '云南中合数维科技有限责任公司'
SEALED_MODULES = {'LLM_service'}  # 运行时按源码执行（每次匹配独立模块），以加密源码发布
SKIP = {'conftest', 'knowledge_eval', 'package_payload', 'build_secure', 'installer'}
EXCLUDES = ['pandas', 'numpy', 'matplotlib', 'scipy']
APP_IMPORTS = ['webview', 'webview.platforms.winforms', 'webview.platforms.edgechromium', 'requests', 'openpyxl', 'xlrd',
               'docx', 'pypdf', 'pypdfium2', 'PIL']


def log(text):
    print('[build]', text, flush=True)


def run(args, **kwargs):
    proc = subprocess.run([str(a) for a in args], **kwargs)
    if proc.returncode:
        raise SystemExit(f'命令失败（{proc.returncode}）：{" ".join(map(str, args[:4]))} …')
    return proc


def own_modules(roots):
    """从入口出发，按 import 语句（含函数内导入）收集项目根目录下的模块。"""
    local = {p.stem for p in ROOT.glob('*.py') if not p.stem.startswith('test_') and p.stem not in SKIP}
    found, queue = set(), [Path(r).stem for r in roots]
    while queue:
        name = queue.pop()
        if name in found or name not in local:
            continue
        found.add(name)
        tree = ast.parse((ROOT / f'{name}.py').read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                queue += [a.name.split('.')[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                queue.append(node.module.split('.')[0])
    return found


def ensure_nuitka():
    if not (TOOLS / 'nuitka').is_dir():
        log('安装 Nuitka 到 ' + str(TOOLS))
        run([sys.executable, '-m', 'pip', 'install', '-q', '--target', TOOLS, NUITKA, 'ordered-set', 'zstandard'])


def compile_modules(names):
    src, out = WORK / 'src', WORK / 'pyd'
    for folder in (src, out):
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
    for name in names:
        shutil.copy2(ROOT / f'{name}.py', src / f'{name}.py')
    env = dict(os.environ, PYTHONPATH=str(TOOLS))

    def build(name):
        run([sys.executable, '-m', 'nuitka', '--module', f'{name}.py', f'--output-dir={out}', '--remove-output',
             '--assume-yes-for-downloads', '--mingw64', '--python-flag=no_docstrings', '--nofollow-imports', '--quiet',
             '--jobs=2'], cwd=src, env=env, stdout=subprocess.DEVNULL)
        return name

    log(f'编译 {len(names)} 个模块为本机代码：' + ', '.join(sorted(names)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(4, (os.cpu_count() or 4) // 2))) as pool:
        for name in pool.map(build, sorted(names, key=lambda n: -(ROOT / f'{n}.py').stat().st_size)):
            log('  已编译 ' + name)
    built = {p.name.split('.')[0]: p for p in out.glob('*.pyd')}
    missing = set(names) - set(built)
    if missing:
        raise SystemExit('编译产物缺失：' + ', '.join(sorted(missing)))
    return built


def seal_sources():
    folder = WORK / 'sealed'
    shutil.rmtree(folder, ignore_errors=True)
    for path in sorted((ROOT / 'skill_templates').rglob('*.py')):
        target = folder / path.relative_to(ROOT).with_suffix(protected.SEALED_SUFFIX)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(protected.seal(path.read_text(encoding='utf-8').encode('utf-8')))
    for name in SEALED_MODULES:
        (folder / f'{name}{protected.SEALED_SUFFIX}').write_bytes(protected.seal((ROOT / f'{name}.py').read_text(encoding='utf-8').encode('utf-8')))
    log(f'加密技能与引擎源码 {len(list(folder.rglob("*" + protected.SEALED_SUFFIX)))} 个')
    return folder


def stub(name, code):
    path = WORK / 'stubs' / f'{name}.py'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding='utf-8')
    return path


def spec_text(script, compiled, analysed, datas, hidden, collect, exe):
    """Analysis 读取项目源码以收集依赖，随后从字节码归档中剔除项目模块，换成编译后的 .pyd。"""
    return f'''# 由 build_secure.py 生成
from PyInstaller.utils.hooks import collect_all
OWN = {sorted(analysed)!r}
datas = {datas!r}
binaries = [(p, '.') for p in {sorted(str(p) for p in compiled.values())!r}]
hiddenimports = {hidden!r}
for package in {collect!r}:
    d, b, h = collect_all(package)
    datas += d; binaries += b; hiddenimports += h
a = Analysis([{str(script)!r}], pathex=[{str(ROOT)!r}, {str(ROOT / 'vendor')!r}], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, excludes={EXCLUDES!r}, noarchive=False, optimize=0)
a.pure = [entry for entry in a.pure if entry[0].split('.')[0] not in OWN]
# 第三方包钩子会附带 .py 源码副本，模块已在字节码归档中，不随包发布任何 .py 文件
a.datas = [entry for entry in a.datas if not entry[0].lower().endswith(('.py', '.pyi'))]
leaked = sorted({{entry[0] for entry in a.pure + a.scripts if entry[0].split('.')[0] in OWN}})
assert not leaked, leaked
pyz = PYZ(a.pure)
{exe}
'''


def version_file(name, description, filename):
    """Windows 文件属性中的版本、开发者与版权信息。"""
    numbers = tuple(int(x) for x in (VERSION.split('.') + ['0'] * 4)[:4])
    path = WORK / 'specs' / f'version-{name}.txt'
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = {'CompanyName': PUBLISHER, 'FileDescription': description, 'FileVersion': VERSION, 'InternalName': name,
              'LegalCopyright': f'© 2026 {PUBLISHER}', 'OriginalFilename': filename, 'ProductName': '电力造价智能体平台', 'ProductVersion': VERSION}
    strings = ', '.join(f'StringStruct({k!r}, {v!r})' for k, v in fields.items())
    path.write_text(f'''VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers!r}, prodvers={numbers!r}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('080404B0', [{strings}])]), VarFileInfo([VarStruct('Translation', [2052, 1200])])]
)
''', encoding='utf-8')
    return str(path)


def pyinstaller(spec_name, text, distpath, workpath):
    spec = WORK / 'specs' / f'{spec_name}.spec'
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(text, encoding='utf-8')
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT / 'build_tools'), str(ROOT / 'vendor')]),
               PYINSTALLER_CONFIG_DIR=str(ROOT / 'build' / 'cache'))
    run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--log-level=WARN', '--distpath', distpath, '--workpath', workpath, spec], env=env)


def build_app(compiled, sealed, analysed):
    from pipeline_skills import ALLOWED_IMPORTS
    icon = str(ROOT / 'assets' / 'app.ico')
    datas = [(str(ROOT / 'assets'), 'assets'), (str(ROOT / 'static'), 'static'), (str(ROOT / 'domain_knowledge'), 'domain_knowledge'), (str(ROOT / 'earthwork_data'), 'earthwork_data'),
             (str(ROOT / 'samples'), 'samples'), (str(ROOT / 'skills.json'), '.'), (str(ROOT / 'VERSION'), '.'),
             (str(sealed / 'skill_templates'), 'skill_templates')] + [(str(sealed / f'{n}{protected.SEALED_SUFFIX}'), '.') for n in SEALED_MODULES]
    # 加密引擎按源码执行，其依赖与技能允许导入的标准库模块须随包发布
    hidden = APP_IMPORTS + sorted(ALLOWED_IMPORTS) + sorted(SEALED_MODULES)
    exe = f'''exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='PowerCostAgent', debug=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=True, icon=[{icon!r}],
          version={version_file('PowerCostAgent', '电力造价智能体平台', 'PowerCostAgent.exe')!r})
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='PowerCostAgent')'''
    launcher = stub('launcher', 'import desktop\ndesktop.run()\n')
    pyinstaller('PowerCostAgent', spec_text(launcher, compiled, analysed, datas, hidden, ['webview', 'pythonnet', 'clr_loader', 'pypdfium2_raw'], exe),
                ROOT / 'dist', ROOT / 'build')
    worker_modules = own_modules(['custom_skills.py'])
    exe = f'''exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='PowerCostSkillWorker', debug=False, strip=False, upx=False,
          runtime_tmpdir=None, console=True, disable_windowed_traceback=True,
          version={version_file('PowerCostSkillWorker', '电力造价智能体平台 技能执行器', 'PowerCostSkillWorker.exe')!r})'''
    worker = stub('worker', 'from custom_skills import worker_main\nworker_main()\n')
    pyinstaller('PowerCostSkillWorker', spec_text(worker, {n: compiled[n] for n in worker_modules}, worker_modules, [], [], [], exe),
                ROOT / 'dist' / 'PowerCostAgent', ROOT / 'build-worker')


def finish_app(compiled):
    app = ROOT / 'dist' / 'PowerCostAgent'
    internal = app / '_internal'
    leaked = [str(p.relative_to(app)) for p in app.rglob('*') if p.suffix.lower() in ('.py', '.pyc', '.pyi')
              and (p.parent in (app, internal) or (internal / 'skill_templates') in p.parents)]
    if leaked:
        raise SystemExit('安装目录中仍有项目源码：' + ', '.join(leaked))
    protected_files = [internal / p.name for p in compiled.values()] + list(internal.rglob('*' + protected.SEALED_SUFFIX)) + \
        [p for p in (internal / 'static').rglob('*') if p.is_file()] + [app / 'PowerCostSkillWorker.exe']
    count = protected.write_manifest(app, protected_files)
    log(f'完整性清单覆盖 {count} 个文件')
    other_py = sorted(str(p.relative_to(app)) for p in app.rglob('*.py'))
    if other_py:
        raise SystemExit('安装目录中仍有 .py 文件：' + ', '.join(other_py[:5]))


def smoke_test():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    result = WORK / 'smoke.json'
    result.unlink(missing_ok=True)
    env = dict(os.environ, POWER_COST_DATA_DIR=tempfile.mkdtemp(prefix='pca-smoke-'), POWER_COST_SMOKE_PASSWORD='admin123!@#',
               POWER_COST_PORT=str(port))
    subprocess.run([ROOT / 'dist' / 'PowerCostAgent' / 'PowerCostAgent.exe', '--smoke-test', result], env=env, timeout=180)
    data = json.loads(result.read_text(encoding='utf-8')) if result.exists() else {}
    if not data.get('composer') or data.get('user') != 'admin' or data.get('earthwork') is not True:
        log_file = Path(str(result) + '.log')
        raise SystemExit('客户端冒烟测试失败：' + json.dumps(data, ensure_ascii=False) + (log_file.read_text(encoding='utf-8')[-1500:] if log_file.exists() else ''))
    log('客户端冒烟测试通过：已用 admin 登录并加载主界面，土石方三维模块（本地 three.js）加载成功')


def build_installer():
    app = ROOT / 'dist' / 'PowerCostAgent'
    payload = ROOT / 'payload.zip'
    with zipfile.ZipFile(payload, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(app.rglob('*')):
            if path.is_file():
                archive.write(path, str(path.relative_to(app)))
    log(f'应用归档 {payload.stat().st_size / 1e6:.1f} MB')
    compiled = compile_modules({'installer'})
    name = f'PowerCostAgent-Setup-{VERSION}'
    for old in (ROOT / 'release').glob('PowerCostAgent-Setup*.exe'):
        old.unlink()
    exe = f'''exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name={name!r}, debug=False, strip=False, upx=False,
          runtime_tmpdir=None, console=False, disable_windowed_traceback=True, icon=[{str(ROOT / 'assets' / 'app.ico')!r}],
          version={version_file('PowerCostAgent-Setup', '电力造价智能体平台 安装程序', name + '.exe')!r})'''
    setup = stub('setup', 'import installer\ninstaller.main()\n')
    pyinstaller('PowerCostAgent-Setup', spec_text(setup, compiled, {'installer'}, [(str(payload), '.'), (str(ROOT / 'VERSION'), '.')], [], [], exe),
                ROOT / 'release', ROOT / 'build-installer')
    target = ROOT / 'release' / f'{name}.exe'
    log(f'安装包：{target}（{target.stat().st_size / 1e6:.1f} MB）SHA256 {hashlib.sha256(target.read_bytes()).hexdigest()}')


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    ensure_nuitka()
    modules = own_modules(APP_ROOTS)
    compiled = compile_modules(modules - SEALED_MODULES)
    sealed = seal_sources()
    build_app(compiled, sealed, modules)
    finish_app(compiled)
    if '--skip-smoke' not in sys.argv:
        smoke_test()
    build_installer()


if __name__ == '__main__':
    main()
