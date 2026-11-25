"""土石方资料清洗 · PDF 预览：页面渲染放在单独的进程里做，结果缓存到磁盘。

CAD 导出的地形图一页可有上百万段线（样例勘察报告的勘探点平面位置图约 160 万段），pdfium 渲染要 10 秒到 2 分钟，
渲染耗时随分辨率增长。所以先出一张低分辨率的图（约 10 秒），再在后台渲染高清图；同一页渲染过一次后直接取缓存。
放在子进程里：不占用服务进程，也不和清洗任务在同一进程里同时调用 pdfium（pdfium 不是线程安全的）。"""
import hashlib
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIDES = (900, 2200)            # 低分辨率、高清图的长边像素
MAX_PROCESSES = 3
TIMEOUT = 600
CACHE = None                   # 由 server 设为数据目录下的缓存目录
_lock = threading.Lock()
_running = {}                  # 缓存键 → (进程, 开始时间)


def _cache_dir():
    path = Path(CACHE) if CACHE else HERE / 'runtime' / 'pdf-preview'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _key(path, *parts):
    stat = path.stat()
    raw = '|'.join([str(path.resolve()).lower(), str(stat.st_mtime_ns), str(stat.st_size)] + [str(p) for p in parts])
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:24]


def _command(args):
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--pdf-preview', *args]
    return [sys.executable, str(HERE / 'desktop.py'), '--pdf-preview', *args]


def _job(key, args, output):
    """确保这项渲染已在进行或已完成。返回 ('ready', 输出文件) | ('pending', 已用秒数) | ('error', 说明)。"""
    if output.is_file():
        return 'ready', output
    error = output.with_suffix('.err')
    with _lock:
        running = _running.get(key)
        if running:
            proc, started = running
            code = proc.poll()
            if code is None:
                if time.time() - started > TIMEOUT:
                    proc.kill()
                    _running.pop(key, None)
                    return 'error', f'渲染超过 {TIMEOUT // 60} 分钟仍未完成，已停止'
                return 'pending', round(time.time() - started)
            _running.pop(key, None)
            if output.is_file():
                return 'ready', output
            return 'error', (error.read_text(encoding='utf-8', errors='replace')[-300:] if error.is_file() else '') or f'渲染进程异常退出（{code}）'
        if error.is_file() and time.time() - error.stat().st_mtime < 30:
            return 'error', error.read_text(encoding='utf-8', errors='replace')[-300:]
        busy = sum(1 for p, _ in _running.values() if p.poll() is None)
        if busy >= MAX_PROCESSES:
            return 'pending', 0
        error.unlink(missing_ok=True)
        flags = 0x08000000 if sys.platform == 'win32' else 0      # 不弹出控制台窗口
        proc = subprocess.Popen(_command(args), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        _running[key] = (proc, time.time())
        return 'pending', 0


def page(path, number, side):
    """第 number 页（从 1 起）长边 side 像素的 PNG。"""
    path = Path(path)
    if side not in SIDES:
        raise ValueError('不支持的预览尺寸')
    key = _key(path, 'page', number, side)
    output = _cache_dir() / f'{key}.png'
    return _job(key, ['render', str(path), str(number), str(side), str(output)], output)


def info(path):
    """页数、默认预览页和各页用途（勘探点平面位置图、钻孔柱状图）。"""
    path = Path(path)
    key = _key(path, 'info', 3)
    output = _cache_dir() / f'{key}.json'
    status, value = _job(key, ['info', str(path), str(output)], output)
    if status == 'ready':
        return status, json.loads(value.read_text(encoding='utf-8'))
    return status, value


# ---------- 子进程入口（desktop.py --pdf-preview …）
def main(argv):
    import earthwork_pdf
    action, source = argv[0], argv[1]
    output = Path(argv[-1])
    temp = output.with_name(output.name + '.part')
    try:
        pdf = earthwork_pdf.open_pdf(source)
        try:
            if action == 'render':
                number, side = int(argv[2]), int(argv[3])
                if not 1 <= number <= len(pdf):
                    raise ValueError('页码超出范围')
                image, _ = earthwork_pdf.render_page(pdf, number - 1, max_side=side)
                temp.write_bytes(image)
            elif action == 'info':
                import earthwork_survey
                texts = [earthwork_pdf.page_text(pdf, i) for i in range(len(pdf))]
                pages = earthwork_survey.classify_pages(pdf, texts)
                default = pages['plan'][0] + 1 if pages['columns'] and pages['plan'] else 1
                if not pages['columns']:
                    # 图纸：取含等高线、高程点图层最多的一页（只看前 3 页）
                    import earthwork_clean
                    default = earthwork_clean._pdf_kind(source).get('page', 1)
                taken = set(pages['plan']) | set(pages['columns'])
                sections = [i + 1 for i, text in enumerate(texts) if i not in taken and '剖面图' in earthwork_survey.squeeze(text)]
                temp.write_text(json.dumps({'pages': len(pdf), 'default': default, 'plan': [p + 1 for p in pages['plan']],
                                            'columns': [p + 1 for p in pages['columns']], 'sections': sections}, ensure_ascii=False), encoding='utf-8')
            else:
                raise ValueError('未知的预览操作')
        finally:
            pdf.close()
        temp.replace(output)
        return 0
    except Exception as exc:
        output.with_suffix('.err').write_text(str(exc)[:500] or exc.__class__.__name__, encoding='utf-8')
        temp.unlink(missing_ok=True)
        return 1
