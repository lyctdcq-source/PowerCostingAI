# -*- coding: utf-8 -*-
"""内容寻址页级特征缓存（PageFeatureCache）。

核心原则：
1. 每份 PDF 在一次解析周期内最多打开一次，多个提取器共享同一份页级文字与版式特征；
2. 目录探查与载体判定阶段严禁调用 get_drawings()，仅读取 words / text / 流字节数；
3. 仅对已锁定的表格页按需懒加载 get_page_drawings() 提取框线线段；
4. 以 (file_size, mtime_ns, sha256_head64KB) 作为内容指纹持久化到磁盘缓存，二次重算毫秒命中。
"""
import hashlib
import json
import os
import threading
from collections import OrderedDict
from pathlib import Path

import pymupdf

_LOCK = threading.RLock()
_DOC_MEM = OrderedDict()       # fingerprint -> doc features dict
_DRAW_MEM = OrderedDict()      # (fingerprint, page_idx) -> drawings summary dict
_MAX_DOC_MEM = 48
_MAX_DRAW_MEM = 256
_CACHE_VERSION = 1

_DEFAULT_CACHE_ROOT = None


def set_cache_root(path):
    """设置默认磁盘缓存根目录（如 DATA/tizi/cache）。"""
    global _DEFAULT_CACHE_ROOT
    _DEFAULT_CACHE_ROOT = Path(path).resolve() if path else None


def get_cache_root():
    return _DEFAULT_CACHE_ROOT


def file_fingerprint(path) -> str:
    """计算 PDF 内容指纹：(size, mtime_ns, sha256_head64KB)。"""
    p = Path(path)
    st = p.stat()
    h = hashlib.sha256()
    h.update(f"{st.st_size}:{st.st_mtime_ns}:".encode("ascii"))
    with open(p, "rb") as f:
        h.update(f.read(65536))
    return h.hexdigest()[:24]


def _page_ink(pg, doc):
    big = 0
    try:
        for im in pg.get_images(full=True):
            big = max(big, (im[2] or 0) * (im[3] or 0))
    except Exception:  # noqa: BLE001
        pass
    size = 0
    try:
        for xref in pg.get_contents():
            size += len(doc.xref_stream_raw(xref) or b"")
    except Exception:  # noqa: BLE001
        pass
    return big, size


def _put_lru(store: OrderedDict, key, val, limit: int):
    store[key] = val
    store.move_to_end(key)
    while len(store) > limit:
        store.popitem(last=False)


def get_doc_features(path, cache_dir=None, include_text=True):
    """获取 PDF 全页文字与版式特征（优先命中内存/磁盘缓存）。"""
    abs_path = os.path.abspath(str(path))
    fp = file_fingerprint(abs_path)

    with _LOCK:
        hit = _DOC_MEM.get(fp)
        if hit is not None:
            _DOC_MEM.move_to_end(fp)
            return hit

    cdir = Path(cache_dir).resolve() if cache_dir else _DEFAULT_CACHE_ROOT
    cache_file = (cdir / f"{fp}.json") if cdir else None
    if cache_file and cache_file.is_file():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if data.get("version") == _CACHE_VERSION and data.get("fingerprint") == fp:
                data["path"] = abs_path
                with _LOCK:
                    _put_lru(_DOC_MEM, fp, data, _MAX_DOC_MEM)
                return data
        except (OSError, json.JSONDecodeError):
            pass

    doc = pymupdf.open(abs_path)
    pages = []
    try:
        for i, pg in enumerate(doc):
            try:
                raw_words = pg.get_text("words")
            except Exception:  # noqa: BLE001
                raw_words = []
            words = [
                [round(w[0], 2), round(w[1], 2), round(w[2], 2), round(w[3], 2),
                 w[4], int(w[5]), int(w[6]), int(w[7])]
                for w in raw_words
            ]
            txt = ""
            if include_text:
                try:
                    txt = pg.get_text("text") or ""
                except Exception:  # noqa: BLE001
                    txt = ""
            chars = len(txt) if include_text else sum(len(w[4]) for w in words)
            big_img, ink_bytes = _page_ink(pg, doc)
            pages.append({
                "page": i,
                "width": round(float(pg.rect.width), 2),
                "height": round(float(pg.rect.height), 2),
                "words": words,
                "text": txt,
                "chars": chars,
                "n_words": len(words),
                "big_img_px": int(big_img),
                "ink_bytes": int(ink_bytes),
            })
    finally:
        doc.close()

    data = {
        "version": _CACHE_VERSION,
        "fingerprint": fp,
        "path": abs_path,
        "n_pages": len(pages),
        "pages": pages,
    }

    if cache_file:
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache_file.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, cache_file)
        except OSError:
            pass

    with _LOCK:
        _put_lru(_DOC_MEM, fp, data, _MAX_DOC_MEM)
    return data


def get_page_drawings(path, page_idx: int):
    """惰性提取单页矢量框线（仅对锁定表格页调用）。

    返回：
      {
        "n_draws": int,
        "h_lines": [(y, x0, x1), ...],
        "v_lines": [(x, y0, y1), ...],
        "rects": [[x0, y0, x1, y1], ...],
      }
    """
    abs_path = os.path.abspath(str(path))
    fp = file_fingerprint(abs_path)
    key = (fp, int(page_idx))

    with _LOCK:
        hit = _DRAW_MEM.get(key)
        if hit is not None:
            _DRAW_MEM.move_to_end(key)
            return hit

    h_lines = []
    v_lines = []
    rects = []
    doc = pymupdf.open(abs_path)
    try:
        if page_idx < 0 or page_idx >= len(doc):
            raise IndexError(f"页码超出范围：{page_idx}")
        pg = doc[int(page_idx)]
        draws = pg.get_drawings()
        n_draws = len(draws)
        for d in draws:
            for item in d.get("items") or []:
                op = item[0]
                if op == "l":
                    p1, p2 = item[1], item[2]
                    dx = abs(p2.x - p1.x)
                    dy = abs(p2.y - p1.y)
                    if dy <= 1.2 and dx >= 4.0:
                        y = round((p1.y + p2.y) / 2.0, 2)
                        h_lines.append((y, round(min(p1.x, p2.x), 2), round(max(p1.x, p2.x), 2)))
                    elif dx <= 1.2 and dy >= 4.0:
                        x = round((p1.x + p2.x) / 2.0, 2)
                        v_lines.append((x, round(min(p1.y, p2.y), 2), round(max(p1.y, p2.y), 2)))
                elif op == "re":
                    r = item[1]
                    x0, y0, x1, y1 = round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2)
                    w, h = x1 - x0, y1 - y0
                    if w <= 1.5 and h >= 4.0:
                        v_lines.append((round((x0 + x1) / 2.0, 2), y0, y1))
                    elif h <= 1.5 and w >= 4.0:
                        h_lines.append((round((y0 + y1) / 2.0, 2), x0, x1))
                    elif w >= 4.0 and h >= 4.0:
                        rects.append([x0, y0, x1, y1])
                        h_lines.append((y0, x0, x1))
                        h_lines.append((y1, x0, x1))
                        v_lines.append((x0, y0, y1))
                        v_lines.append((x1, y0, y1))
    finally:
        doc.close()

    res = {
        "n_draws": n_draws,
        "h_lines": h_lines,
        "v_lines": v_lines,
        "rects": rects,
    }
    with _LOCK:
        _put_lru(_DRAW_MEM, key, res, _MAX_DRAW_MEM)
    return res


def clear_memory_cache():
    """清空进程内缓存（主要用于测试隔离）。"""
    with _LOCK:
        _DOC_MEM.clear()
        _DRAW_MEM.clear()
