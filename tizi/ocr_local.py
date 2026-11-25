# -*- coding: utf-8 -*-
"""本机离线局部 OCR 辅助器（严禁联网、不调在线视觉模型）。

仅用于图纸框选无文字层区域（如非标基础详图材料表）时，返回候选文本供人工核对确认（status="pending"）。
优先尝试本机已安装的 rapidocr_onnxruntime / pytesseract；不可用时使用 Windows 常驻 WinRT OcrEngine worker（消除 PowerShell 冷启动）；
均不可用时优雅降级返回 available=False，绝不抛 500 错误阻塞手工录入。
"""
import atexit
import os
import subprocess
import tempfile
import threading

import pymupdf

_RAPID_OCR = None
_RAPID_CHECKED = False


class _WinRtOcrWorker:
    """Windows 常驻轻量 WinRT OcrEngine 子进程池，避免每次框选 1~2s PowerShell 冷启动（修复 D-11）。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._proc = None

    def _ensure_started(self):
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        ps_loop = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) { $m = $asTaskGeneric.MakeGenericMethod($type); $t = $m.Invoke($null, @($op)); $t.Wait(); $t.Result }
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { exit 2 }
while ($null -ne ($line = [Console]::In.ReadLine())) {
    if ($line -eq '__EXIT__') { break }
    try {
        $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($line)) ([Windows.Storage.StorageFile])
        $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $res = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        $stream.Dispose()
        Write-Output $res.Text
    } catch {
        Write-Output ''
    }
    Write-Output '<<<TIZI_OCR_END>>>'
}
"""
        self._proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", ps_loop],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="ignore",
            bufsize=1,
        )
        return self._proc

    def recognize_file(self, img_path: str) -> str | None:
        with self._lock:
            try:
                proc = self._ensure_started()
                assert proc.stdin is not None and proc.stdout is not None
                proc.stdin.write(img_path + "\n")
                proc.stdin.flush()
                lines = []
                while True:
                    ln = proc.stdout.readline()
                    if not ln:
                        self.close()
                        return None
                    s = ln.rstrip("\r\n")
                    if s == "<<<TIZI_OCR_END>>>":
                        break
                    lines.append(s)
                return "\n".join(lines).strip()
            except Exception:  # noqa: BLE001
                self.close()
                return None

    def close(self):
        with self._lock:
            if self._proc is not None:
                try:
                    if self._proc.poll() is None:
                        self._proc.terminate()
                except Exception:  # noqa: BLE001
                    pass
                self._proc = None


_WINRT_WORKER = _WinRtOcrWorker()
atexit.register(_WINRT_WORKER.close)


def ocr_pdf_region(path: str, page: int, bbox: list, dpi: int = 260):
    """对 PDF 指定区域做本机离线 OCR，返回 {"available", "engine", "text", "lines", "reason"}。"""
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ValueError("bbox 必须为 4 个数值")

    doc = pymupdf.open(path)
    try:
        if page < 0 or page >= len(doc):
            raise IndexError("页码超出范围")
        pg = doc[int(page)]
        clip = pymupdf.Rect(*[float(v) for v in bbox])
        if clip.is_empty:
            return {"available": True, "engine": "none", "text": "", "lines": [], "reason": "选区为空"}
        scale = max(150, min(int(dpi), 320)) / 72.0
        pix = pg.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip, alpha=False)
        png_bytes = pix.tobytes("png")
    finally:
        doc.close()

    # 1) 优先尝试 rapidocr_onnxruntime（跨平台且纯离线）
    global _RAPID_OCR, _RAPID_CHECKED
    if not _RAPID_CHECKED:
        _RAPID_CHECKED = True
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-not-found]
            _RAPID_OCR = RapidOCR()
        except Exception:  # noqa: BLE001
            _RAPID_OCR = None
    if _RAPID_OCR is not None:
        try:
            res, _ = _RAPID_OCR(png_bytes)
            lines = [str(item[1]).strip() for item in (res or []) if len(item) >= 2 and str(item[1]).strip()]
            return {
                "available": True, "engine": "rapidocr",
                "text": "\n".join(lines), "lines": lines,
                "reason": "" if lines else "RapidOCR 未识别出文字，请人工照图录入",
            }
        except Exception:  # noqa: BLE001
            pass

    # 2) 其次尝试 pytesseract（若用户本机已装）
    try:
        import io
        import pytesseract
        from PIL import Image
        img = Image.open(io.BytesIO(png_bytes))
        txt = (pytesseract.image_to_string(img, lang="chi_sim+eng") or "").strip()
        lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
        return {
            "available": True, "engine": "tesseract",
            "text": "\n".join(lines), "lines": lines,
            "reason": "" if lines else "离线 OCR 未识别出文字，请人工照图录入",
        }
    except Exception:  # noqa: BLE001
        pass

    # 3) Windows 本机常驻 WinRT OcrEngine worker（完全离线，低延迟复用）
    if os.name == "nt":
        tmp_png = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                tf.write(png_bytes)
                tmp_png = tf.name
            txt = _WINRT_WORKER.recognize_file(tmp_png)
            if txt is not None:
                lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
                return {
                    "available": True, "engine": "windows_ocr",
                    "text": "\n".join(lines), "lines": lines,
                    "reason": "" if lines else "Windows 离线 OCR 未识别出文字，请人工照图录入",
                }
        except Exception:  # noqa: BLE001
            pass
        finally:
            if tmp_png:
                try:
                    os.unlink(tmp_png)
                except OSError:
                    pass

    return {
        "available": False,
        "engine": "unavailable",
        "text": "",
        "lines": [],
        "reason": "本机未检测到离线 OCR 引擎（未安装 Tesseract 且 WinRT OCR 不可用），请直接照图手工录入。",
    }
