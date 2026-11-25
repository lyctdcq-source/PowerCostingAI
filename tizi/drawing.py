# -*- coding: utf-8 -*-
"""图纸读取解析与磁盘 LRU 页面渲染缓存。"""
import hashlib
import logging
import os
import re
import threading
from pathlib import Path

import pymupdf

from . import cache as feat_cache

_LOG = logging.getLogger("tizi")

ANCHOR_MAP = {
    "铁塔型号": "杆塔型号", "杆塔型号": "杆塔型号", "塔型": "杆塔型号",
    "呼称高": "呼称高", "呼高": "呼称高",
    "铁塔耗钢": "全重", "耗钢": "全重", "单基重": "全重", "塔重": "全重",
    "基础型号": "基础型号A", "基础型式": "基础型号A",
    "接地装置": "接地装置型号",
    "线路转角": "线路转角", "转角度数": "线路转角",
    "杆塔号": "杆塔号", "塔号": "杆塔号",
    "桩号": "位置桩号", "定位高差": "定位高差",
    "接腿": "是否高低腿", "高低腿": "是否高低腿",
}

DATA_RE = re.compile(r"^[\dA-Za-z\.\-（）()/×x*°%]+$")
MAX_DPI = 200
_PAGE_CACHE_MAX = 200
_CACHE_LOCK = threading.RLock()
_RENDER_CACHE_DIR = None


def set_render_cache_dir(path):
    global _RENDER_CACHE_DIR
    _RENDER_CACHE_DIR = Path(path).resolve() if path else None


def _ink(pg, doc):
    big = 0
    try:
        for im in pg.get_images(full=True):
            big = max(big, (im[2] or 0) * (im[3] or 0))
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("drawing._ink get_images failed: %s", exc)
    size = 0
    try:
        for x in pg.get_contents():
            size += len(doc.xref_stream_raw(x) or b"")
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("drawing._ink get_contents failed: %s", exc)
    return big, size


def _merge_lines(words, y_gap=4.0, x_gap=10.0):
    if not words:
        return []
    ws = sorted(words, key=lambda w: (round(w[1] / y_gap), w[0]))
    lines, cur = [], []
    for w in ws:
        if not cur:
            cur = [w]
            continue
        last = cur[-1]
        same_y = abs(w[1] - last[1]) <= y_gap
        near_x = w[0] - last[2] <= x_gap
        if same_y and near_x:
            cur.append(w)
        else:
            lines.append(cur)
            cur = [w]
    if cur:
        lines.append(cur)
    out = []
    for ln in lines:
        bbox = [min(w[0] for w in ln), min(w[1] for w in ln),
                max(w[2] for w in ln), max(w[3] for w in ln)]
        out.append({
            "bbox": [round(v, 1) for v in bbox],
            "text": "".join(w[4] for w in ln),
            "n_words": len(ln),
        })
    return out


def _anchor_of(text):
    t = (text or "").strip()
    if len(t) > 14:
        return None, None
    if re.match(r"^[0-9一二三四五六七八九十]+[.、．]", t):
        return None, None
    for key, target in ANCHOR_MAP.items():
        if key in t:
            return key, target
    return None, None


def probe(path):
    """逐页探针（复用 PageFeatureCache，零重复开文件）。"""
    doc = feat_cache.get_doc_features(path)
    pages = []
    for pg in doc["pages"]:
        words = pg["words"]
        big = pg["big_img_px"]
        ink_kb = round(pg["ink_bytes"] / 1024, 1)
        chars = pg["chars"]
        lines = _merge_lines(words)
        anchors = []
        for ln in lines:
            key, target = _anchor_of(ln["text"])
            if key:
                anchors.append({"label": key, "target": target, "bbox": ln["bbox"]})

        if len(words) >= 3:
            cap = "text_layer"
            hint = f"文字层可读 {chars} 字符，但表格数值可能是矢量路径"
        elif ink_kb >= 50 and big < 1_000_000:
            cap = "vector_only"
            hint = "无文字层；文字与数字被画成矢量路径，程序读不出，需人工看图或局部离线 OCR"
        elif big >= 1_000_000:
            cap = "scanned"
            hint = "整页位图（扫描件），需 OCR 才能识字"
        else:
            cap = "empty"
            hint = "页面内容很少，无法判定"

        pages.append({
            "page": pg["page"],
            "size": [round(pg["width"]), round(pg["height"])],
            "n_words": len(words),
            "chars": chars,
            "big_img_px": big,
            "ink_kb": ink_kb,
            "capability": cap,
            "hint": hint,
            "anchors": anchors,
        })

    caps = {p["capability"] for p in pages}
    if caps == {"vector_only"}:
        overall = "vector_only"
    elif caps == {"scanned"}:
        overall = "scanned"
    elif "text_layer" in caps:
        overall = "partial_text"
    else:
        overall = "mixed"

    return {
        "file": os.path.basename(path),
        "pages": pages,
        "n_pages": len(pages),
        "overall": overall,
        "n_anchors": sum(len(p["anchors"]) for p in pages),
        "summary": {
            "vector_only": "纯矢量图，数值无法由程序直接读取",
            "scanned": "扫描件，需要 OCR",
            "partial_text": "有可读取的标题/栏目文字层，数值仍可能需人工框选",
            "mixed": "各页情况不同，逐页查看",
        }[overall],
    }


def labels(path, page=0):
    doc = feat_cache.get_doc_features(path)
    if page < 0 or page >= doc["n_pages"]:
        raise IndexError("页码超出范围")
    pg = doc["pages"][page]
    lines = _merge_lines(pg["words"])
    out = []
    for ln in lines:
        key, target = _anchor_of(ln["text"])
        out.append({
            "bbox": ln["bbox"],
            "text": ln["text"],
            "kind": "anchor" if key else ("data" if DATA_RE.match(ln["text"]) else "note"),
            "anchor": key,
            "target": target,
        })
    return {
        "page": page, "lines": out,
        "n_words": len(pg["words"]),
        "size": [round(pg["width"]), round(pg["height"])],
    }


def region_words(path, page, bbox):
    doc = feat_cache.get_doc_features(path)
    if page < 0 or page >= doc["n_pages"]:
        raise IndexError("页码超出范围")
    pg = doc["pages"][page]
    r = pymupdf.Rect(*[float(v) for v in bbox])
    words = [w for w in pg["words"] if pymupdf.Rect(w[:4]).intersects(r)]
    lines = _merge_lines(words)
    has_text = len(lines) > 0
    return {
        "page": page,
        "bbox": [round(v, 1) for v in r],
        "lines": lines,
        "text": "\n".join(ln["text"] for ln in lines),
        "has_text": has_text,
        "reason": "" if has_text else (
            "该区域无文字层：数值被画成矢量路径或位图，程序读不出。"
            "请在界面上直接填写数值，或使用局部离线 OCR 辅助识别。"),
        "ink_kb": round(pg["ink_bytes"] / 1024, 1),
    }


def _evict_lru(cdir: Path):
    try:
        pngs = sorted(cdir.glob("*.png"), key=lambda p: p.stat().st_mtime)
        while len(pngs) > _PAGE_CACHE_MAX:
            victim = pngs.pop(0)
            try:
                victim.unlink(missing_ok=True)
            except OSError as exc:
                _LOG.debug("page cache unlink failed (%s): %s", victim, exc)
    except OSError as exc:
        _LOG.debug("page cache evict scan failed (%s): %s", cdir, exc)


def render_page(path, page=0, dpi=110, clip=None, highlight=None, cache_dir=None):
    """渲染成 PNG 字节，支持磁盘 LRU 缓存（上限 200 张）。"""
    dpi = max(36, min(int(dpi), MAX_DPI))
    cdir = Path(cache_dir).resolve() if cache_dir else _RENDER_CACHE_DIR
    cache_file = None
    if cdir:
        fp = feat_cache.file_fingerprint(path)
        key_parts = []
        if highlight:
            key_parts.append("h:" + ",".join(f"{float(v):.1f}" for v in highlight))
        if clip:
            key_parts.append("c:" + ",".join(f"{float(v):.1f}" for v in clip))
        hkey = ("_" + hashlib.md5("|".join(key_parts).encode("ascii")).hexdigest()[:10]) if key_parts else ""
        cache_file = cdir / f"{fp}_p{int(page)}_d{dpi}{hkey}.png"
        if cache_file.is_file():
            try:
                os.utime(cache_file, None)
                data = cache_file.read_bytes()
                w = int.from_bytes(data[16:20], "big")
                h = int.from_bytes(data[20:24], "big")
                return data, [w, h]
            except OSError as exc:
                _LOG.debug("page cache read failed (%s): %s", cache_file, exc)

    doc = pymupdf.open(path)
    try:
        if page < 0 or page >= len(doc):
            raise IndexError("页码超出范围")
        pg = doc[page]
        clip_rect = None
        if clip:
            raw_clip = pymupdf.Rect(*[float(v) for v in clip]) & pg.rect
            if not raw_clip.is_empty and raw_clip.width >= 20 and raw_clip.height >= 20:
                clip_rect = raw_clip

        if highlight:
            hr = pymupdf.Rect(*[float(v) for v in highlight])
            if not hr.is_empty:
                shape = pg.new_shape()
                shape.draw_rect(hr)
                shape.finish(color=(0.886, 0.294, 0.290), width=1.8)
                shape.commit()

        scale = dpi / 72.0
        pix = pg.get_pixmap(matrix=pymupdf.Matrix(scale, scale),
                            clip=clip_rect, alpha=False)
        data = pix.tobytes("png")
        size = [pix.width, pix.height]
    finally:
        doc.close()

    if cache_file:
        with _CACHE_LOCK:
            try:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                tmp = cache_file.with_suffix(".png.tmp")
                tmp.write_bytes(data)
                os.replace(tmp, cache_file)
                _evict_lru(cache_file.parent)
            except OSError as exc:
                _LOG.warning("page cache write failed (%s): %s", cache_file, exc)
    return data, size
